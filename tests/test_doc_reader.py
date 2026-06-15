"""Loose, format-resolving document reader (.docx → markdown, stdlib)."""

import zipfile

from src.data.doc_reader import docx_to_markdown, read_document

_NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')


def _docx(tmp_path, body, *, rels="", numbering="", name="d.docx") -> str:
    p = tmp_path / name
    doc = (f'<?xml version="1.0"?><w:document {_NS}><w:body>{body}'
           f'</w:body></w:document>')
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("word/document.xml", doc)
        if rels:
            z.writestr("word/_rels/document.xml.rels", rels)
        if numbering:
            z.writestr("word/numbering.xml", numbering)
    return str(p)


def _p(runs, ppr=""):
    return f"<w:p>{ppr}{runs}</w:p>"


def _r(text, rpr=""):
    return f"<w:r>{rpr}<w:t xml:space=\"preserve\">{text}</w:t></w:r>"


class TestFormatting:
    def test_headings(self, tmp_path):
        body = (_p(_r("Overview"), '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>')
                + _p(_r("Details"), '<w:pPr><w:pStyle w:val="Heading 2"/></w:pPr>'))
        md = docx_to_markdown(_docx(tmp_path, body))
        assert "# Overview" in md
        assert "## Details" in md

    def test_bold_italic_strike(self, tmp_path):
        body = _p(_r("bold", "<w:rPr><w:b/></w:rPr>")
                  + _r(" and ")
                  + _r("ital", "<w:rPr><w:i/></w:rPr>")
                  + _r(" and ")
                  + _r("gone", "<w:rPr><w:strike/></w:rPr>"))
        md = docx_to_markdown(_docx(tmp_path, body))
        assert "**bold**" in md and "*ital*" in md and "~~gone~~" in md

    def test_toggle_off_not_bold(self, tmp_path):
        body = _p(_r("plain", '<w:rPr><w:b w:val="false"/></w:rPr>'))
        md = docx_to_markdown(_docx(tmp_path, body))
        assert "**" not in md and "plain" in md

    def test_hyperlink(self, tmp_path):
        body = _p('<w:hyperlink r:id="rId7">' + _r("the doc") + '</w:hyperlink>')
        rels = ('<?xml version="1.0"?><Relationships '
                'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId7" Target="https://x.test/guide"/></Relationships>')
        md = docx_to_markdown(_docx(tmp_path, body, rels=rels))
        assert "[the doc](https://x.test/guide)" in md

    def test_bullet_and_numbered_lists(self, tmp_path):
        numbering = (
            f'<?xml version="1.0"?><w:numbering {_NS}>'
            '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0">'
            '<w:numFmt w:val="bullet"/></w:lvl></w:abstractNum>'
            '<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0">'
            '<w:numFmt w:val="decimal"/></w:lvl></w:abstractNum>'
            '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
            '<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>'
            '</w:numbering>')
        body = (
            _p(_r("first bullet"),
               '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>')
            + _p(_r("step one"),
                 '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr>'))
        md = docx_to_markdown(_docx(tmp_path, body, numbering=numbering))
        assert "- first bullet" in md
        assert "1. step one" in md

    def test_nested_list_indent(self, tmp_path):
        numbering = (
            f'<?xml version="1.0"?><w:numbering {_NS}>'
            '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0">'
            '<w:numFmt w:val="bullet"/></w:lvl></w:abstractNum>'
            '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>')
        body = _p(_r("child"),
                  '<w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="1"/></w:numPr></w:pPr>')
        md = docx_to_markdown(_docx(tmp_path, body, numbering=numbering))
        assert "  - child" in md   # indented one level

    def test_table(self, tmp_path):
        cell = lambda t: f"<w:tc>{_p(_r(t))}</w:tc>"
        row = lambda a, b: f"<w:tr>{cell(a)}{cell(b)}</w:tr>"
        body = f"<w:tbl>{row('Tier', 'Billing')}{row('A', 'Flat')}</w:tbl>"
        md = docx_to_markdown(_docx(tmp_path, body))
        assert "| Tier | Billing |" in md
        assert "| --- | --- |" in md
        assert "| A | Flat |" in md


class TestDispatch:
    def test_read_document_docx(self, tmp_path):
        body = _p(_r("hello"), '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>')
        assert "# hello" in read_document(_docx(tmp_path, body))

    def test_read_document_md_passthrough(self, tmp_path):
        f = tmp_path / "n.md"
        f.write_text("# Title\n\n- a\n- b")
        out = read_document(str(f))
        assert out == "# Title\n\n- a\n- b"

    def test_read_document_txt(self, tmp_path):
        f = tmp_path / "n.txt"
        f.write_text("just text")
        assert read_document(str(f)) == "just text"

    def test_loose_tolerant_on_garbage(self, tmp_path):
        p = tmp_path / "bad.docx"
        p.write_bytes(b"not a zip")
        out = read_document(str(p))
        assert "can't be read" in out   # degrades, never raises


class TestFlowsIntoCard:
    def test_docx_formatting_survives_into_card(self, tmp_path):
        from src.data.doc_to_card import card_from_document
        body = (_p(_r("Overview"), '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>')
                + _p(_r("verify ") + _r("secondary", "<w:rPr><w:b/></w:rPr>")
                     + _r(" coverage")))
        md = read_document(_docx(tmp_path, body, name="COB.docx"))
        title, card = card_from_document("COB.docx", md)
        assert "# Overview" in card                 # heading preserved
        assert "**secondary**" in card              # bold preserved
        assert title.startswith("COB")
