"""Asana list calls must follow next_page.offset to the end — and survive a
short-lived offset token expiring mid-walk.

Bug: list_projects (and the other single-shot list calls) sent limit=100 and
never read next_page, so an org with 2000+ projects saw only the first 100.

Offset-token health (owner callout): Asana's pagination offset is short-lived
and cannot be persisted; presenting a stale offset returns HTTP 400. A long
walk can outlive its own cursor, so the pager must return the partial results
gathered so far and log — never crash the whole discovery.
"""

import json

import pytest

from src.data import asana_client as ac


class _Resp:
    def __init__(self, body):
        self._b = body

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _page(items, offset=None):
    body = {"data": items}
    if offset is not None:
        body["next_page"] = {"offset": offset, "path": "x", "uri": "x"}
    return json.dumps(body).encode()


def _offset_of(url):
    """Return the offset= query value in a URL, or None."""
    import urllib.parse as up
    q = up.parse_qs(up.urlparse(url).query)
    return q.get("offset", [None])[0]


def _install(monkeypatch, pages_by_offset, *, expire_after=None):
    """pages_by_offset maps the incoming offset (None for page 1) to
    (items, next_offset). expire_after: raise a 400 when this offset arrives,
    simulating a dead cursor."""
    calls = {"n": 0, "offsets": []}

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        if "/projects" not in url:
            return _Resp(_page([]))
        off = _offset_of(url)
        calls["n"] += 1
        calls["offsets"].append(off)
        if expire_after is not None and off == expire_after:
            raise ac.urllib.error.HTTPError(
                url, 400, "Bad Request", {},
                _body_stream('{"errors":[{"message":"pagination token expired"}]}'))
        items, nxt = pages_by_offset[off]
        return _Resp(_page(items, nxt))

    monkeypatch.setattr(ac.urllib.request, "urlopen", fake_urlopen)
    return calls


def _body_stream(text):
    import io
    return io.BytesIO(text.encode())


def _projects(n, start=0):
    return [{"gid": str(start + i), "name": f"P{start + i}"} for i in range(n)]


# ── the core bug: full multi-page read ────────────────────────────────

def test_list_projects_follows_pagination_across_pages(monkeypatch):
    calls = _install(monkeypatch, {
        None: (_projects(100, 0), "tok2"),
        "tok2": (_projects(100, 100), "tok3"),
        "tok3": (_projects(30, 200), None),
    })
    projects = ac.AsanaClient("k").list_projects("W1")
    assert len(projects) == 230, "pagination stopped short of the full set"
    assert projects[0]["gid"] == "0" and projects[-1]["gid"] == "229"
    assert calls["n"] == 3, "expected three page fetches"
    assert calls["offsets"] == [None, "tok2", "tok3"], "offset token not followed"


def test_single_page_does_not_request_a_second(monkeypatch):
    calls = _install(monkeypatch, {None: (_projects(40), None)})
    assert len(ac.AsanaClient("k").list_projects("W1")) == 40
    assert calls["n"] == 1, "made an unnecessary second request with no next_page"


# ── offset-token health: expiry mid-walk returns partial, never crashes ──

def test_expired_offset_midwalk_returns_partial_and_warns(monkeypatch, caplog):
    calls = _install(monkeypatch, {
        None: (_projects(100, 0), "tok2"),
        "tok2": (_projects(100, 100), "tok3"),
        # tok3 would be page 3, but the cursor dies before it is served
    }, expire_after="tok3")
    with caplog.at_level("WARNING"):
        projects = ac.AsanaClient("k").list_projects("W1")
    # Two full pages survived; the third (dead cursor) is dropped, not fatal.
    assert len(projects) == 200
    assert calls["offsets"] == [None, "tok2", "tok3"]
    assert any("offset expired" in r.message.lower() for r in caplog.records), \
        "an expired offset must be logged, not swallowed silently"


def test_first_page_400_is_not_swallowed(monkeypatch):
    """A 400 on the FIRST request has no offset in play — it is a real request
    error and must propagate, not be masked as an empty result."""
    def fake_urlopen(req, timeout=None):
        if "/projects" in req.full_url:
            raise ac.urllib.error.HTTPError(
                req.full_url, 400, "Bad Request", {},
                _body_stream('{"errors":[{"message":"invalid workspace"}]}'))
        return _Resp(_page([]))

    monkeypatch.setattr(ac.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ac.urllib.error.HTTPError):
        ac.AsanaClient("k").list_projects("W1")


# ── the other formerly single-shot calls now paginate too ─────────────

def test_list_workspaces_paginates(monkeypatch):
    pages = {None: ([{"gid": "W1", "name": "A"}], "t2"),
             "t2": ([{"gid": "W2", "name": "B"}], None)}

    def fake_urlopen(req, timeout=None):
        off = _offset_of(req.full_url)
        items, nxt = pages[off]
        return _Resp(_page(items, nxt))

    monkeypatch.setattr(ac.urllib.request, "urlopen", fake_urlopen)
    ws = ac.AsanaClient("k").list_workspaces()
    assert [w["gid"] for w in ws] == ["W1", "W2"]


def test_list_subtasks_paginates(monkeypatch):
    pages = {None: ([{"gid": "s1", "name": "a", "completed": False}], "t2"),
             "t2": ([{"gid": "s2", "name": "b", "completed": True}], None)}

    def fake_urlopen(req, timeout=None):
        off = _offset_of(req.full_url)
        items, nxt = pages[off]
        return _Resp(_page(items, nxt))

    monkeypatch.setattr(ac.urllib.request, "urlopen", fake_urlopen)
    subs = ac.AsanaClient("k").list_subtasks("T1")
    assert [s["gid"] for s in subs] == ["s1", "s2"]


# ── page cap is a logged truncation, never silent ─────────────────────

def test_page_cap_is_logged(monkeypatch, caplog):
    # Every page returns a next offset forever; the cap must stop it and warn.
    def fake_urlopen(req, timeout=None):
        if "/projects" not in req.full_url:
            return _Resp(_page([]))
        return _Resp(_page(_projects(100), "always-more"))

    monkeypatch.setattr(ac.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(ac, "_PROJECTS_MAX_PAGES", 3)
    with caplog.at_level("WARNING"):
        projects = ac.AsanaClient("k").list_projects("W1")
    assert len(projects) == 300  # 3 pages × 100
    assert any("page cap" in r.message.lower() for r in caplog.records), \
        "hitting the page cap must be logged, not a silent truncation"
