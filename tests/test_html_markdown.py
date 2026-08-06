"""HTML ↔ markdown conversion — stdlib fallback + Qt path + round trip."""

import pytest

from src.data.html_markdown import (
    _MarkdownParser, _restore_task_markers, html_to_markdown,
    markdown_to_html,
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

    def test_task_list_renders_glyphs(self):
        html = markdown_to_html("- [ ] todo\n- [x] done\n")
        assert "<input" not in html
        assert html.count('class="task-list-item"') == 2
        assert "☐ todo" in html
        assert "☑ done" in html

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


class TestListFormatParity:
    """Loose (blank-line-separated) lists must render as tight <li> items —
    Chromium honors <li><p> margins (double-spaced steps on Guru/Zendesk)
    while Qt collapses them, so the shared converter normalizes to tight."""

    def test_loose_ordered_list_emits_tight_items(self):
        html = markdown_to_html("1. Step one\n\n2. Step two")
        assert "<li>Step one</li>" in html
        assert "<li>Step two</li>" in html
        assert "<p>" not in html

    def test_loose_bullet_list_emits_tight_items(self):
        html = markdown_to_html("- Item A\n\n- Item B")
        assert "<li>Item A</li>" in html
        assert "<li>Item B</li>" in html
        assert "<p>" not in html

    def test_multi_paragraph_item_keeps_paragraphs(self):
        html = markdown_to_html(
            "- First para\n\n    Second para of same item\n\n- Another item")
        assert "<p>First para</p>" in html
        assert "<p>Second para of same item</p>" in html
        assert "<li>Another item</li>" in html

    def test_leading_paragraph_before_nested_list_unwrapped(self):
        html = markdown_to_html(
            "1. Parent step\n\n    - child a\n    - child b\n\n2. Next")
        assert "<p>Parent step</p>" not in html
        assert "Parent step" in html
        assert "<li>child a</li>" in html
        assert "<li>Next</li>" in html

    def test_tight_task_items_emit_glyphs_not_inputs(self):
        html = markdown_to_html("- [ ] Do A\n- [x] Do B\n")
        assert "<input" not in html
        assert html.count('class="task-list-item"') == 2
        assert "☐ Do A" in html
        assert "☑ Do B" in html

    def test_loose_task_items_emit_glyphs_not_inputs(self):
        html = markdown_to_html("- [ ] Do A\n\n- [x] Do B\n")
        assert "<input" not in html
        assert html.count('class="task-list-item"') == 2
        assert "☐ Do A" in html
        assert "☑ Do B" in html
        assert "[ ]" not in html and "[x]" not in html

    def test_task_glyph_round_trip(self):
        html = markdown_to_html("- [ ] Do A\n- [x] Do B\n")
        md = html_to_markdown(html)
        assert "- [ ] Do A" in md
        assert "- [x] Do B" in md


class TestTaskGlyphFenceAwareness:
    """_restore_task_markers must not rewrite glyph lines inside code
    fences: a fenced sample containing '- ☐ item' is CODE someone wrote,
    not a rendered task list, and must round-trip byte-identical (it seeds
    _article_body_text and the zendesk body_text clipboard flavour). Only
    backtick fences count — both converter paths (Qt toMarkdown and the
    stdlib fallback) emit ``` fences exclusively, and a line starting
    '~~~' is Qt strikethrough ('~~' + text beginning '~'), never a
    converter-produced fence."""

    def test_fenced_glyph_lines_survive_restore(self):
        md = ("- ☐ real task\n\n"
              "```\n"
              "- ☐ literal glyph\n"
              "- ☑ another literal\n"
              "```\n\n"
              "- ☑ done task")
        out = _restore_task_markers(md)
        assert "- [ ] real task" in out
        assert "- [x] done task" in out
        # inside the fence: byte-identical, never mapped to checkboxes
        assert "- ☐ literal glyph" in out
        assert "- ☑ another literal" in out
        assert "[ ] literal glyph" not in out
        assert "[x] another literal" not in out

    def test_language_info_fence_is_still_a_fence(self):
        md = "```python\n- ☐ item\n```\n\n- ☐ outside"
        out = _restore_task_markers(md)
        assert "- ☐ item" in out
        assert "- [ ] outside" in out

    def test_unclosed_fence_runs_to_end_of_document(self):
        md = "- ☐ before\n\n```\n- ☐ inside, fence never closes"
        out = _restore_task_markers(md)
        assert "- [ ] before" in out
        assert "- ☐ inside, fence never closes" in out

    def test_indented_fence_inside_list_item(self):
        # Qt indents fences inside list items — still a fence.
        md = "- item\n  ```\n  - ☐ code\n  ```\n- ☐ after"
        out = _restore_task_markers(md)
        assert "- ☐ code" in out
        assert "- [ ] after" in out

    def test_tilde_run_is_not_a_fence(self):
        # '~~~struck~~' is strikethrough of '~struck', not a fence opener;
        # it must not suppress restoration for the rest of the document.
        md = "~~~struck~~\n\n- ☐ task"
        out = _restore_task_markers(md)
        assert "- [ ] task" in out
        assert "~~~struck~~" in out

    def test_inline_code_span_line_is_not_a_fence(self):
        # A one-line span like ```foo``` is not an opener (a backtick
        # fence's info string cannot contain backticks).
        md = "```code span```\n\n- ☐ task"
        out = _restore_task_markers(md)
        assert "- [ ] task" in out

    def test_html_to_markdown_fallback_pre_keeps_glyphs_literal(
            self, monkeypatch):
        # Pin the STDLIB fallback path headless even when an earlier test
        # created a QApplication: no QGuiApplication → parser path.
        from PySide6.QtGui import QGuiApplication
        monkeypatch.setattr(QGuiApplication, "instance",
                            staticmethod(lambda: None))
        html = ("<ul><li>☐ real task</li></ul>"
                "<pre>- ☐ keep literal\n- ☑ also literal</pre>")
        md = html_to_markdown(html)
        assert "- [ ] real task" in md
        assert "- ☐ keep literal" in md
        assert "- ☑ also literal" in md
        assert "[ ] keep literal" not in md
        assert "[x] also literal" not in md
