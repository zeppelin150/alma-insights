"""to_asana_html: markdown → Asana html_notes dialect (WS-D-WEB editing).

Asana rejects the whole PUT on any tag outside its subset, so the serializer
must MAP or UNWRAP everything — these tests lock the dialect rules."""

from src.data.asana_rich import to_asana_html


def test_bold_em_and_paragraphs_no_p_tags():
    out = to_asana_html("**bold** and *em*\n\nsecond para")
    assert out.startswith("<body>") and out.endswith("</body>")
    assert "<strong>bold</strong>" in out
    assert "<em>em</em>" in out
    assert "<p>" not in out and "</p>" not in out
    assert "second para" in out


def test_lists_survive():
    out = to_asana_html("- one\n- two")
    assert "<ul>" in out and out.count("<li>") == 2


def test_http_links_kept_others_unwrapped():
    out = to_asana_html("[doc](https://x.example/d) [evil](javascript:alert(1))")
    assert '<a href="https://x.example/d">doc</a>' in out
    assert "javascript:" not in out
    assert "evil" in out            # label survives as plain text
    assert '<a href="javascript' not in out


def test_deep_headings_map_to_h2():
    out = to_asana_html("### Section")
    assert "<h2>Section</h2>" in out
    assert "<h3>" not in out


def test_text_is_xml_escaped():
    out = to_asana_html("a < b & c > d")
    assert "a &lt; b &amp; c &gt; d" in out


def test_raw_html_script_content_dropped():
    out = to_asana_html("hello <script>steal()</script> world")
    assert "steal" not in out
    assert "hello" in out and "world" in out
    assert "<script" not in out


def test_unknown_tags_unwrap_children():
    out = to_asana_html("<div><span>kept text</span></div>")
    assert "kept text" in out
    assert "<div" not in out and "<span" not in out


def test_empty_and_none_are_safe():
    assert to_asana_html("") == "<body></body>"
    assert to_asana_html(None) == "<body></body>"


if __name__ == "__main__":
    import sys

    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
