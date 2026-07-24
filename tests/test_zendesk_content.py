"""E5 — Zendesk Help-Center + macros: client endpoints (mocked urlopen),
store sync/draft/publish-updates-not-creates, demo seed, page tab."""

import json
import urllib.error
from email.message import Message

import pytest

from src.data import zendesk_store


# ── client (mocked urlopen) ──────────────────────────────────────────

class _Resp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode()
    def read(self):
        return self._p
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


@pytest.fixture()
def client():
    from src.data.zendesk_client import ZendeskClient
    return ZendeskClient("acme", "agent@acme.com", "key123")


def _install(monkeypatch, handler):
    import src.data.zendesk_client as zc
    monkeypatch.setattr(zc.urllib.request, "urlopen",
                        lambda req, timeout=0: handler(req))


class TestClientReads:
    def test_get_articles(self, client, monkeypatch):
        _install(monkeypatch, lambda req: _Resp(
            {"articles": [{"id": 1, "title": "SSO"}]}))
        arts = client.get_articles()
        assert arts[0]["id"] == 1

    def test_list_macros(self, client, monkeypatch):
        _install(monkeypatch, lambda req: _Resp(
            {"macros": [{"id": 9, "title": "Reset"}]}))
        assert client.list_macros()[0]["id"] == 9


class TestClientWrites:
    def test_update_article_uses_translation_endpoint(self, client, monkeypatch):
        seen = {}
        def handler(req):
            seen["url"] = req.full_url
            seen["method"] = req.method
            seen["body"] = json.loads(req.data.decode())
            return _Resp({"translation": {"title": "X"}})
        _install(monkeypatch, handler)
        client.update_article(55, title="X", body="<p>hi</p>")
        assert "/articles/55/translations/en-us.json" in seen["url"]
        assert seen["method"] == "PUT"
        assert seen["body"]["translation"]["title"] == "X"

    def test_create_macro_posts(self, client, monkeypatch):
        seen = {}
        def handler(req):
            seen["method"] = req.method
            seen["body"] = json.loads(req.data.decode())
            return _Resp({"macro": {"id": 77}})
        _install(monkeypatch, handler)
        out = client.create_macro("Reset", [{"field": "comment_value", "value": "hi"}])
        assert out["id"] == 77 and seen["method"] == "POST"
        assert seen["body"]["macro"]["title"] == "Reset"


# ── store ────────────────────────────────────────────────────────────

class FakeZendesk:
    def __init__(self):
        self.created, self.updated = [], []
        self.articles = [{"id": 101, "title": "SSO", "body": "<p>b</p>",
                          "locale": "en-us", "section_id": 9, "updated_at": "t"}]
        self.macros = [{"id": 201, "title": "Reset", "actions": [], "active": True,
                        "updated_at": "t"}]
    def get_articles(self, locale="en-us"):
        return self.articles
    def list_macros(self):
        return self.macros
    def update_article(self, article_id, *, title=None, body=None, locale="en-us"):
        self.updated.append(("article", article_id)); return {"id": article_id}
    def create_article(self, section_id, title, body, *, locale="en-us"):
        self.created.append(("article", title)); return {"id": 999}
    def update_macro(self, macro_id, *, name=None, actions=None):
        self.updated.append(("macro", macro_id)); return {"id": macro_id}
    def create_macro(self, name, actions, *, description=None):
        self.created.append(("macro", name)); return {"id": 888}


class StubLLM:
    def __init__(self, payload):
        self.payload = payload
    def generate(self, prompt):
        return self.payload


class TestSync:
    def test_sync_articles_and_macros(self, empty_db):
        conn = empty_db.conn
        z = FakeZendesk()
        assert zendesk_store.sync_articles(conn, z)["count"] == 1
        assert zendesk_store.sync_macros(conn, z)["count"] == 1
        assert zendesk_store.list_articles(conn)[0]["article_id"] == 101
        assert zendesk_store.list_macros(conn)[0]["macro_id"] == 201

    def test_sync_delegates_to_upserts_populating_projections(self, empty_db):
        """Mig-051: sync rows must carry body_text/content_hash (the upsert
        path), not the bare mig-030 column set."""
        conn = empty_db.conn
        zendesk_store.sync_articles(conn, FakeZendesk())
        row = conn.execute(
            "SELECT body_text, content_hash, body_html, body "
            "FROM zendesk_articles WHERE article_id=101").fetchone()
        assert row[0] == "b"                 # <p>b</p> stripped
        assert row[1]                        # content_hash populated
        assert row[2] == "<p>b</p>"          # verbatim body_html
        assert row[3] == "<p>b</p>"          # legacy body column still fed
        zendesk_store.sync_macros(conn, FakeZendesk())
        mrow = conn.execute(
            "SELECT actions_text, content_hash FROM zendesk_macros "
            "WHERE macro_id=201").fetchone()
        assert mrow[0] is not None and mrow[1]


class TestArticleDrafts:
    def test_draft_from_document(self, empty_db):
        from src.data.enablement_store import save_document
        conn = empty_db.conn
        doc = save_document(conn, source="drive", name="SSO.gdoc", full_text="SSO body")
        llm = StubLLM("TITLE: SSO Article\n---\n## Steps\n1. open")
        res = zendesk_store.draft_article_from_document(conn, doc, llm)
        assert res["ok"]
        d = zendesk_store.get_article_draft(conn, res["draft_id"])
        assert d["title"] == "SSO Article" and "Steps" in d["body"]

    def test_publish_updates_linked_article(self, empty_db):
        conn = empty_db.conn
        z = FakeZendesk()
        did = zendesk_store.save_article_draft(conn, title="SSO", body="# b",
                                               article_id=101)
        res = zendesk_store.publish_article_draft(conn, did, zendesk_client=z)
        assert res["ok"]
        assert z.updated == [("article", 101)] and not z.created
        assert zendesk_store.get_article_draft(conn, did)["status"] == "pushed"

    def test_publish_creates_when_unlinked(self, empty_db):
        conn = empty_db.conn
        z = FakeZendesk()
        did = zendesk_store.save_article_draft(conn, title="New", body="# b",
                                               section_id=9)
        res = zendesk_store.publish_article_draft(conn, did, zendesk_client=z)
        assert res["ok"] and z.created == [("article", "New")]

    def test_publish_demo_marks_pushed(self, empty_db):
        conn = empty_db.conn
        did = zendesk_store.save_article_draft(conn, title="x", body="b")
        res = zendesk_store.publish_article_draft(conn, did, zendesk_client=None)
        assert res["ok"]
        assert zendesk_store.get_article_draft(conn, did)["status"] == "pushed"


class TestMacroDrafts:
    def test_draft_from_document_tolerant(self, empty_db):
        from src.data.enablement_store import save_document
        conn = empty_db.conn
        doc = save_document(conn, source="drive", name="reset.gdoc", full_text="x")
        llm = StubLLM('here: {"name": "Reset", "actions": [{"field": "status", "value": "solved"}]}')
        res = zendesk_store.draft_macro_from_document(conn, doc, llm)
        assert res["ok"] and res["name"] == "Reset"

    def test_publish_updates_linked_macro(self, empty_db):
        conn = empty_db.conn
        z = FakeZendesk()
        did = zendesk_store.save_macro_draft(conn, name="R", actions=[], macro_id=201)
        res = zendesk_store.publish_macro_draft(conn, did, zendesk_client=z)
        assert res["ok"] and z.updated == [("macro", 201)]


class TestDemoSeed:
    def test_seed(self, empty_db):
        from src.data.enablement_sim import seed_demo_zendesk
        out = seed_demo_zendesk(empty_db.conn)
        assert out["articles"] == 3 and out["macros"] == 2
        assert len(zendesk_store.list_article_drafts(empty_db.conn)) == 1


@pytest.mark.ui
class TestPage:
    """Locked native-tab contracts. The web_tabs flag is PINNED off here so
    a 'zendesk'/'all' settings value (the dev box runs 'all') can never route
    these assertions at the WebHost surface — the flag-on branch is owned by
    tests/test_zendesk_web_tab.py."""

    def test_enablement_has_zendesk_tab(self, empty_db, monkeypatch):
        monkeypatch.setattr("src.ui.web.web_flags.web_tabs_mode", lambda: "off")
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from src.ui.pages.enablement import EnablementPage
        page = EnablementPage(empty_db, demo=True)
        assert "zendesk" in page._tab_widgets
        page.select_tab("zendesk")
        assert page.zendesk._a_list.count() >= 1   # demo article draft
        assert page.zendesk._m_list.count() >= 1   # demo macro draft

    def test_article_push_demo_marks_pushed(self, empty_db, monkeypatch):
        monkeypatch.setattr("src.ui.web.web_flags.web_tabs_mode", lambda: "off")
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from src.ui.pages.enablement import EnablementPage
        page = EnablementPage(empty_db, demo=True)
        drafts = zendesk_store.list_article_drafts(page._conn())
        page._on_zd_article_push(drafts[0]["id"])
        assert zendesk_store.get_article_draft(page._conn(), drafts[0]["id"])["status"] == "pushed"
