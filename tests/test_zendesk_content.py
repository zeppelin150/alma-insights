"""E5 — Zendesk Help-Center + macros: client endpoints (mocked urlopen),
store sync/draft/local-only publish, demo seed, page tab.

ONE-WAY ZENDESK POLICY (locked owner decision) — the old push contracts
this file used to lock (PUT-to-translations, update-if-linked-else-create,
"demo marks pushed") are RETIRED. The Zendesk API is import/read only:
nothing in the app may POST/PUT/DELETE against Zendesk, and content
reaches Zendesk only when a specialist pastes it in by hand. The
assertions below lock that instead — ``publish_*`` performs zero network
activity, ignores any ``zendesk_client`` it is handed, and only moves the
local draft status. Do not "restore" the push tests.
"""

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


class TestClientIsReadOnly:
    """Replaces the retired TestClientWrites. The client carries no article
    or macro write surface at all — there is nothing left to call."""

    _WRITE_METHODS = ("create_article", "update_article", "delete_article",
                      "create_macro", "update_macro", "delete_macro")

    def test_no_article_or_macro_write_methods(self, client):
        present = [m for m in self._WRITE_METHODS if hasattr(client, m)]
        assert present == [], f"Zendesk write surface resurrected: {present}"

    def test_reads_still_work(self, client, monkeypatch):
        """The ingestion/import lane is unaffected by the policy."""
        _install(monkeypatch, lambda req: _Resp({"articles": [{"id": 5}]}))
        assert client.get_articles()[0]["id"] == 5


# ── store ────────────────────────────────────────────────────────────

class FakeZendesk:
    """Reads are real; the write methods are kept ON PURPOSE so the publish
    tests below can prove they are never reached even when a fully capable
    client is handed to publish_*."""

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

    def test_publish_of_a_linked_draft_never_updates_remotely(self, empty_db):
        """Retired contract: a linked draft used to PUT the live article.
        One-way policy — it is now a local status move, nothing else."""
        conn = empty_db.conn
        z = FakeZendesk()
        did = zendesk_store.save_article_draft(conn, title="SSO", body="# b",
                                               article_id=101)
        res = zendesk_store.publish_article_draft(conn, did, zendesk_client=z)
        assert res["ok"] and res["remote_write"] is False
        assert res["result"] is None
        assert z.updated == [] and z.created == []
        d = zendesk_store.get_article_draft(conn, did)
        assert d["status"] == "copied" and d["copied_at"]
        assert d["article_id"] == 101          # link preserved, not re-fetched

    def test_publish_of_an_unlinked_draft_never_creates_remotely(self, empty_db):
        """Retired contract: an unlinked draft used to POST a new article,
        and a missing section was an error. Neither happens now."""
        conn = empty_db.conn
        z = FakeZendesk()
        did = zendesk_store.save_article_draft(conn, title="New", body="# b")
        res = zendesk_store.publish_article_draft(conn, did, zendesk_client=z)
        assert res["ok"] and res["remote_write"] is False
        assert z.created == [] and z.updated == []
        assert res["article_id"] is None
        assert zendesk_store.get_article_draft(conn, did)["status"] == "copied"

    def test_publish_marks_copied_locally_without_a_client(self, empty_db):
        conn = empty_db.conn
        did = zendesk_store.save_article_draft(conn, title="x", body="b")
        res = zendesk_store.publish_article_draft(conn, did, zendesk_client=None)
        assert res["ok"] and res["status"] == "copied"
        assert zendesk_store.get_article_draft(conn, did)["status"] == "copied"

    def test_publish_is_idempotent_and_reads_legacy_pushed_rows(self, empty_db):
        """'pushed' rows written by the retired path stay readable and count
        as already handled — they are never re-processed."""
        conn = empty_db.conn
        did = zendesk_store.save_article_draft(conn, title="x", body="b")
        conn.execute("UPDATE zendesk_article_drafts SET status='pushed' "
                     "WHERE id=?", (did,))
        conn.commit()
        res = zendesk_store.publish_article_draft(conn, did, zendesk_client=None)
        assert res["ok"] and res["already"] is True
        assert res["status"] == "pushed" and res["remote_write"] is False
        assert zendesk_store.get_article_draft(conn, did)["status"] == "pushed"

    def test_publish_drops_the_draft_off_the_pending_list(self, empty_db):
        conn = empty_db.conn
        did = zendesk_store.save_article_draft(conn, title="x", body="b")
        assert [d["id"] for d in zendesk_store.list_article_drafts(conn)] == [did]
        zendesk_store.publish_article_draft(conn, did)
        assert zendesk_store.list_article_drafts(conn) == []


class TestMacroDrafts:
    def test_draft_from_document_tolerant(self, empty_db):
        from src.data.enablement_store import save_document
        conn = empty_db.conn
        doc = save_document(conn, source="drive", name="reset.gdoc", full_text="x")
        llm = StubLLM('here: {"name": "Reset", "actions": [{"field": "status", "value": "solved"}]}')
        res = zendesk_store.draft_macro_from_document(conn, doc, llm)
        assert res["ok"] and res["name"] == "Reset"

    def test_publish_of_a_linked_macro_never_updates_remotely(self, empty_db):
        """Retired contract: a linked macro draft used to PUT the live macro."""
        conn = empty_db.conn
        z = FakeZendesk()
        did = zendesk_store.save_macro_draft(conn, name="R", actions=[], macro_id=201)
        res = zendesk_store.publish_macro_draft(conn, did, zendesk_client=z)
        assert res["ok"] and res["remote_write"] is False
        assert res["result"] is None
        assert z.updated == [] and z.created == []
        d = zendesk_store.get_macro_draft(conn, did)
        assert d["status"] == "copied" and d["copied_at"]
        assert d["macro_id"] == 201

    def test_publish_marks_copied_locally_without_a_client(self, empty_db):
        conn = empty_db.conn
        did = zendesk_store.save_macro_draft(conn, name="R", actions=[])
        res = zendesk_store.publish_macro_draft(conn, did, zendesk_client=None)
        assert res["ok"] and res["status"] == "copied"
        assert zendesk_store.get_macro_draft(conn, did)["status"] == "copied"


class TestPublishIsAirgapped:
    """Regression lock for the one-way policy: publish_* must perform ZERO
    network activity and must not touch the client it is handed, no matter
    what that client can do."""

    def test_publish_never_touches_a_mock_client_with_write_methods(self, empty_db):
        from unittest.mock import MagicMock
        conn = empty_db.conn
        client = MagicMock()                  # every attribute is callable
        aid = zendesk_store.save_article_draft(conn, title="T", body="b",
                                               article_id=42)
        mid = zendesk_store.save_macro_draft(conn, name="M", actions=[],
                                             macro_id=99)
        zendesk_store.publish_article_draft(conn, aid, zendesk_client=client,
                                            section_id=9)
        zendesk_store.publish_macro_draft(conn, mid, zendesk_client=client)
        assert client.mock_calls == [], f"client was used: {client.mock_calls}"
        assert client.method_calls == []

    def test_publish_opens_no_socket_even_with_a_real_client(self, empty_db,
                                                             monkeypatch):
        """A real ZendeskClient with real credentials still yields no HTTP."""
        import src.data.zendesk_client as zc
        from src.data.zendesk_client import ZendeskClient

        def _boom(*a, **k):                    # any request is a policy breach
            raise AssertionError("publish_* attempted a network request")

        monkeypatch.setattr(zc.urllib.request, "urlopen", _boom)
        conn = empty_db.conn
        did = zendesk_store.save_article_draft(conn, title="T", body="b",
                                               article_id=7)
        res = zendesk_store.publish_article_draft(
            conn, did, zendesk_client=ZendeskClient("acme", "a@acme.com", "k"))
        assert res["ok"] and res["remote_write"] is False

    def test_store_source_holds_no_zendesk_write_call(self):
        """Grep guard: the retired client write methods must not reappear in
        the store. The ``_draft`` suffixed local functions are the legitimate
        namesakes and are excluded by the lookaheads."""
        import re
        from pathlib import Path
        src = Path(zendesk_store.__file__).read_text(encoding="utf-8")
        pattern = (r"\bcreate_article\b|\bupdate_article(?!_draft)\b"
                   r"|\bcreate_macro\b|\bupdate_macro(?!_draft)\b"
                   r"|\bdelete_article\b|\bdelete_macro\b")
        hits = re.findall(pattern, src)
        assert hits == [], f"Zendesk write call resurrected: {hits}"


class TestDemoSeed:
    def test_seed(self, empty_db):
        from src.data.enablement_sim import seed_demo_zendesk
        out = seed_demo_zendesk(empty_db.conn)
        assert out["articles"] == 3 and out["macros"] == 2
        assert len(zendesk_store.list_article_drafts(empty_db.conn)) == 1
        assert len(zendesk_store.list_macro_drafts(empty_db.conn)) == 1


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

    def test_article_push_handler_marks_copied_locally(self, empty_db, monkeypatch):
        """Retired contract: this used to mark 'pushed'. The handler now runs
        the local-only store path and lands the draft in 'copied'."""
        monkeypatch.setattr("src.ui.web.web_flags.web_tabs_mode", lambda: "off")
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from src.ui.pages.enablement import EnablementPage
        page = EnablementPage(empty_db, demo=True)
        drafts = zendesk_store.list_article_drafts(page._conn())
        page._on_zd_article_push(drafts[0]["id"])
        d = zendesk_store.get_article_draft(page._conn(), drafts[0]["id"])
        assert d["status"] == "copied"

    def test_tab_offers_copy_out_not_publish(self, empty_db, monkeypatch):
        """The classic tab's affordance must not suggest publishing."""
        monkeypatch.setattr("src.ui.web.web_flags.web_tabs_mode", lambda: "off")
        from PySide6.QtWidgets import QApplication, QPushButton
        QApplication.instance() or QApplication([])
        from src.ui.pages.enablement.zendesk_tab import ZendeskPage
        tab = ZendeskPage()
        labels = [b.text().lower() for b in tab.findChildren(QPushButton)]
        assert not any("push" in t for t in labels), labels
        assert labels.count("copy for zendesk") == 2      # articles + macros
        assert labels.count("mark as copied") == 2

    def test_tab_copy_button_puts_the_body_on_the_clipboard(self, empty_db,
                                                            monkeypatch):
        monkeypatch.setattr("src.ui.web.web_flags.web_tabs_mode", lambda: "off")
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from src.ui.pages.enablement.zendesk_tab import ZendeskPage
        tab = ZendeskPage()
        tab.show_article_draft({"id": 1, "title": "T", "body": "hello world",
                                "status": "pending"})
        tab._a_copy.click()
        assert "hello world" in QGuiApplication.clipboard().text()

    def test_tab_shows_terminal_states_and_disables_the_mark_button(
            self, empty_db, monkeypatch):
        monkeypatch.setattr("src.ui.web.web_flags.web_tabs_mode", lambda: "off")
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from src.ui.pages.enablement.zendesk_tab import ZendeskPage
        tab = ZendeskPage()
        tab.show_article_draft({"id": 1, "title": "T", "body": "b",
                                "status": "copied"})
        assert tab._a_push.text() == "Copied" and not tab._a_push.isEnabled()
        # legacy rows written by the retired push path stay readable
        tab.show_article_draft({"id": 2, "title": "T", "body": "b",
                                "status": "pushed"})
        assert tab._a_push.text() == "Pushed" and not tab._a_push.isEnabled()
        tab.show_article_draft({"id": 3, "title": "T", "body": "b",
                                "status": "pending"})
        assert tab._a_push.text() == "Mark as copied" and tab._a_push.isEnabled()
