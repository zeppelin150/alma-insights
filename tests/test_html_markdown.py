"""HTML ↔ markdown conversion — stdlib fallback + Qt path + round trip."""

import pytest

from src.data.html_markdown import (
    _MarkdownParser, html_to_markdown, markdown_to_html,
)

GURU_HTML = (
    "<h1>Payments v2</h1>"
    "<p>Unified remittance ledger with <strong>auto-matching</strong> for "
    "<em>ERA</em> lines. See <a href='https://example.com/doc'>the doc</a>.</p>"
    "<h2>Steps</h2>"
    "<ol><li>Open the ledger</li><li>Review matches</li></ol>"
    "<ul><li>Tier A unchanged</li><li>Tier B usage-based</li></ul>"
    "<blockquote>Rollout: June 30, 2026</blockquote>"
    "<pre>code block</pre>"
    "<table><tr><th>Tier</th><th>Billing</th></tr>"
    "<tr><td>A</td><td>Flat</td></tr><tr><td>B</td><td>Usage</td></tr></table>"
)


def _fallback(html: str) -> str:
    p = _MarkdownParser()
    p.feed(html)
    p.close()
    return p.result()


class TestFallbackParser:
    def test_headings_and_emphasis(self):
        md = _fallback(GURU_HTML)
        assert "# Payments v2" in md
        assert "## Steps" in md
        assert "**auto-matching**" in md
        assert "*ERA*" in md

    def test_links(self):
        md = _fallback(GURU_HTML)
        assert "[the doc](https://example.com/doc)" in md

    def test_lists(self):
        md = _fallback(GURU_HTML)
        assert "1. Open the ledger" in md
        assert "2. Review matches" in md
        assert "- Tier A unchanged" in md

    def test_blockquote_code_table(self):
        md = _fallback(GURU_HTML)
        assert "> Rollout: June 30, 2026" in md
        assert "```\ncode block\n```" in md
        assert "| Tier | Billing |" in md
        assert "| --- | --- |" in md
        assert "| B | Usage |" in md

    def test_script_and_style_skipped(self):
        md = _fallback("<p>keep</p><script>alert(1)</script><style>p{}</style>")
        assert "keep" in md
        assert "alert" not in md

    def test_empty(self):
        assert html_to_markdown("") == ""
        assert html_to_markdown("   ") == ""


@pytest.mark.ui
class TestQtPath:
    def test_qt_conversion_when_app_exists(self):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        md = html_to_markdown("<h2>Title</h2><p>Body <strong>bold</strong></p>")
        assert "Title" in md and "**bold**" in md


class TestMarkdownToHtml:
    def test_round_trip_essentials(self):
        html = markdown_to_html("## Steps\n\n1. One\n2. Two\n\n| A | B |\n|---|---|\n| 1 | 2 |")
        assert "<h2>Steps</h2>" in html
        assert "<ol>" in html
        assert "<table>" in html

    def test_strikethrough_renders(self):
        # GFM ~~text~~ → <del> (the editor emits this; base markdown does not).
        html = markdown_to_html("this is ~~struck~~ text")
        assert "<del>struck</del>" in html

    def test_task_list_renders_checkboxes(self):
        html = markdown_to_html("- [ ] todo\n- [x] done\n")
        assert html.count('type="checkbox"') == 2
        assert "checked" in html                       # the done row
        assert 'class="task-list-item"' in html

    def test_no_pymdownx_dependency(self):
        # The post-pass must stay within installed deps (pymdownx absent).
        with pytest.raises(ImportError):
            __import__("pymdownx")
        # …and markdown_to_html must still work without it.
        assert "<del>x</del>" in markdown_to_html("~~x~~")

    def test_fenced_code_and_hr(self):
        html = markdown_to_html("```python\nx = 1\n```\n\n---\n")
        assert "<pre>" in html or "<code>" in html
        assert "<hr" in html
