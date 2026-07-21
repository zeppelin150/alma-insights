"""XSS corpus + benign-markup preservation tests for src/data/html_sanitize.

The sanitizer is the first of two independent layers (the second is the
sandboxed, script-disabled preview iframe). These tests assert the deny-by-
default posture: nothing executable, no unsafe schemes, no style smuggling —
while the markup Guru/Zendesk cards actually use survives.
"""

import pytest

from src.data.html_sanitize import sanitize_html


# ── script execution vectors ─────────────────────────────────────────

def test_script_tag_dropped_with_content():
    out = sanitize_html("before<script>alert(1)</script>after")
    assert "<script" not in out.lower()
    assert "alert(1)" not in out
    assert "before" in out and "after" in out


def test_style_tag_dropped_with_content():
    out = sanitize_html("<style>@import url(evil.css);</style>ok")
    assert "@import" not in out and "<style" not in out.lower()
    assert "ok" in out


def test_event_handlers_stripped_from_allowed_tags():
    out = sanitize_html('<b onclick="alert(1)" onmouseover=alert(2)>bold</b>')
    assert out == "<b>bold</b>"


def test_img_onerror_stripped_and_bad_src_dropped():
    out = sanitize_html('<img src="x" onerror="alert(1)">')
    assert "onerror" not in out.lower()
    assert 'src="x"' not in out


def test_svg_and_foreignobject_dropped_entirely():
    out = sanitize_html(
        '<svg onload="alert(1)"><foreignObject><b>x</b></foreignObject></svg>rest')
    assert "<svg" not in out.lower() and "foreignobject" not in out.lower()
    assert "alert(1)" not in out
    assert "rest" in out


def test_iframe_object_embed_form_dropped_with_content():
    src = ('<iframe src="https://x"></iframe><object data="x">o</object>'
           '<embed src="x"><form action="x"><input value="v"></form>tail')
    out = sanitize_html(src)
    for bad in ("<iframe", "<object", "<embed", "<form", "<input"):
        assert bad not in out.lower()
    assert "tail" in out


def test_nested_tag_smuggling_does_not_reassemble():
    # After the parser drops the inner <script>, the leftovers must be inert
    # escaped text — no character sequence that could open ANY live tag.
    import re
    out = sanitize_html("<scr<script>ipt>alert(1)</scr</script>ipt>")
    assert "<script" not in out.lower()
    assert re.search(r"<[a-zA-Z/]", out) is None


def test_template_and_noscript_dropped():
    out = sanitize_html("<template><img src=x onerror=alert(1)></template>"
                        "<noscript><b>n</b></noscript>keep")
    assert "onerror" not in out.lower()
    assert "keep" in out


# ── URL scheme vectors ───────────────────────────────────────────────

def test_javascript_href_dropped():
    out = sanitize_html('<a href="javascript:alert(1)">x</a>')
    assert "javascript:" not in out.lower()
    assert "href" not in out.lower()
    assert ">x</a>" in out


def test_data_html_href_dropped_but_https_kept():
    out = sanitize_html('<a href="data:text/html,<script>1</script>">x</a>'
                        '<a href="https://guru.example/card">y</a>')
    assert "data:text/html" not in out
    assert 'href="https://guru.example/card"' in out


def test_img_data_image_kept_data_html_dropped():
    ok = sanitize_html('<img src="data:image/png;base64,iVBORw0KGgo=">')
    assert 'src="data:image/png;base64,iVBORw0KGgo="' in ok
    bad = sanitize_html('<img src="data:text/html;base64,PHNjcmlwdD4=">')
    assert "src" not in bad.lower()


def test_vbscript_and_file_schemes_dropped():
    out = sanitize_html('<a href="vbscript:x">a</a><a href="file:///etc/passwd">b</a>')
    assert "vbscript" not in out.lower() and "file://" not in out.lower()


# ── style smuggling ──────────────────────────────────────────────────

def test_style_url_and_expression_banned():
    out = sanitize_html(
        '<span style="background-color:url(javascript:alert(1));color:red">x</span>')
    assert "url(" not in out.lower()
    assert "color: red" in out          # the benign declaration survives


def test_style_unknown_props_dropped():
    out = sanitize_html('<span style="position:fixed;top:0;color:#356859">x</span>')
    assert "position" not in out.lower() and "top" not in out.lower()
    assert "color: #356859" in out


def test_style_only_on_style_tags():
    out = sanitize_html('<a href="https://x.example" style="color:red">x</a>')
    assert "style=" not in out.lower()


# ── structural hygiene ───────────────────────────────────────────────

def test_comments_and_cdata_dropped():
    out = sanitize_html("a<!-- <script>alert(1)</script> -->b<![CDATA[<b>]]>c")
    assert "script" not in out.lower()
    assert "a" in out and "b" in out and "c" in out


def test_meta_refresh_and_base_dropped():
    out = sanitize_html('<meta http-equiv="refresh" content="0;url=https://evil">'
                        '<base href="https://evil/">x')
    assert "refresh" not in out.lower() and "<base" not in out.lower()
    assert "x" in out


def test_unknown_tags_unwrapped_content_kept():
    out = sanitize_html("<article><section>body text</section></article>")
    assert "<article" not in out and "<section" not in out
    assert "body text" in out


def test_unbalanced_input_yields_balanced_output():
    out = sanitize_html("<b><i>text")
    assert out == "<b><i>text</i></b>"


def test_stray_close_tags_dropped():
    assert sanitize_html("</b>x</div>") == "x"


def test_attribute_breakout_is_escaped():
    out = sanitize_html('<b title=\'" onmouseover="alert(1)\'>x</b>')
    assert "onmouseover" not in out or "&quot;" in out
    assert out.count("<b") == 1


def test_empty_and_none_safe():
    assert sanitize_html("") == ""
    assert sanitize_html(None) == ""


# ── benign Guru/Zendesk markup survives ──────────────────────────────

def test_benign_card_markup_preserved():
    src = ("<h2>Steps</h2><p>Open <b>Admin Console</b> &amp; go to "
           "<a href=\"https://help.example/sso\">SSO setup</a>.</p>"
           "<ul><li>One</li><li>Two</li></ul>"
           "<table><thead><tr><th>Plan</th></tr></thead>"
           "<tbody><tr><td colspan=\"2\" align=\"center\">All</td></tr></tbody></table>"
           "<pre><code>metadata.xml</code></pre>"
           "<blockquote>Rollout: June 24</blockquote>")
    out = sanitize_html(src)
    for keep in ("<h2>", "<b>Admin Console</b>", 'href="https://help.example/sso"',
                 "<ul><li>One</li>", 'colspan="2"', 'align="center"',
                 "<pre><code>metadata.xml</code></pre>", "<blockquote>"):
        assert keep in out
    assert "&amp;" in out


def test_highlight_colors_survive():
    out = sanitize_html('<span style="background-color:#FFF3CD; color:#356859">hi</span>')
    assert "background-color: #FFF3CD" in out and "color: #356859" in out


def test_text_entities_do_not_become_tags():
    out = sanitize_html("<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>")
    assert "<script" not in out.lower()
    assert "&lt;script&gt;" in out


# ── skip-stack edge cases (M6 review fix #8) ─────────────────────────

def test_unmatched_drop_close_does_not_un_suppress():
    # </textarea> must NOT end the <form> suppression — the form's content
    # stays dropped (it would otherwise leak, though still sanitized).
    out = sanitize_html("<form></textarea>leak<b>y</b></form>tail")
    assert "leak" not in out
    assert "tail" in out


def test_unclosed_drop_container_does_not_eat_the_document():
    # an allowed ancestor closing implicitly closes the stray <form>/<select>,
    # so trailing card content still renders (was: whole preview emptied)
    assert sanitize_html("<div><form><b>hidden</b></div>after") == "<div></div>after"
    assert "more text" in sanitize_html("<div><select><option>x</div>more text")


def test_self_closing_allowed_tag_kept_as_open():
    # HTML5 ignores the self-closing slash on non-void tags: <td/> == <td>
    out = sanitize_html("<table><tbody><tr><td/>cell</tr></tbody></table>")
    assert "<td>" in out and "cell" in out
    assert sanitize_html("<div/>content") == "<div>content</div>"


def test_self_closing_drop_tag_suppresses_nothing():
    out = sanitize_html("before<script/>after")
    assert "<script" not in out.lower()
    assert "before" in out and "after" in out


def test_properly_closed_form_still_fully_dropped():
    out = sanitize_html("keep<form><input><button>x</button></form>keep2")
    assert "<form" not in out.lower() and "<input" not in out.lower()
    assert "keep" in out and "keep2" in out


def test_parser_failure_fails_closed(monkeypatch):
    import src.data.html_sanitize as hs

    class Boom(hs._Sanitizer):
        def feed(self, *_a, **_k):
            raise RuntimeError("parser blew up")

    monkeypatch.setattr(hs, "_Sanitizer", Boom)
    out = hs.sanitize_html("<b>x</b>")
    assert "<b>" not in out and "&lt;b&gt;" in out


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
