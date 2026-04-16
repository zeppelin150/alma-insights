"""
Tests for GuruCardViewer widget and helpers.

Covers: card rendering, media placeholders, redline overlays,
HTML-to-markdown export, chrome metadata, and the public
guru_card_to_html() helper.
"""

import pytest
from src.ui.widgets.guru_card_viewer import (
    GuruCardViewer,
    guru_card_to_html,
    _sanitize_card_content,
    _html_to_approximate_md,
    _media_placeholder,
    _escape,
    GAP_LABELS,
)


# ── Fixtures ──────────────────────────────────────────────────────

SAMPLE_CARD = {
    "id": "card-001",
    "title": "Billing Deduction Process",
    "content": (
        "<h2>Overview</h2>"
        "<p>This card outlines the standard process for handling "
        "provider billing deductions.</p>"
        "<h2>Resolution Steps</h2>"
        "<ol>"
        "<li><strong>Step 1:</strong> Verify the provider.</li>"
        "<li><strong>Step 2:</strong> Navigate to Billing.</li>"
        "</ol>"
    ),
    "collection": "Billing & Payments",
    "collection_id": "col-123",
    "lastModified": "2026-02-14T10:30:00Z",
    "status": "TRUSTED",
    "owner": "CX Ops",
}

SAMPLE_CARD_MARKDOWN = {
    "id": "card-002",
    "title": "Plain Markdown Card",
    "content": "## Heading\n\nSome **bold** text and a [link](https://example.com).",
    "collection": "Docs",
    "lastModified": "2026-03-01",
    "status": "NEEDS_VERIFICATION",
}

SAMPLE_CARD_WITH_MEDIA = {
    "id": "card-003",
    "title": "Card with Media",
    "content": (
        '<h2>Setup Guide</h2>'
        '<p>Follow these steps:</p>'
        '<img src="https://example.com/screenshot.png" alt="Portal Screenshot">'
        '<p>Then configure the settings:</p>'
        '<video controls><source src="video.mp4"></video>'
        '<iframe title="Demo Widget" src="https://example.com/widget"></iframe>'
    ),
    "collection": "Onboarding",
    "lastModified": "2026-01-20",
    "status": "UNVERIFIED",
}

SAMPLE_REDLINES = [
    {
        "type": "insert",
        "html": "<strong>Step 3a (NEW):</strong> Check the Source column for AUTO-v2.4.",
    },
    {
        "type": "modify",
        "title": "Step 5: Escalation",
        "old": "Escalate to Billing Support L2.",
        "new": "Escalate to the Billing Automation Team (not L2).",
    },
    {
        "type": "warning",
        "text": "23 tickets suggest deductions apply before the billing cycle closes.",
    },
]


# ── Content Sanitization ─────────────────────────────────────────

class TestSanitizeCardContent:
    """Tests for _sanitize_card_content()."""

    def test_html_content_preserved(self):
        html = "<h2>Title</h2><p>Body text</p>"
        result = _sanitize_card_content(html)
        assert "<h2>Title</h2>" in result
        assert "<p>Body text</p>" in result

    def test_img_replaced_with_placeholder(self):
        html = '<p>Before</p><img src="pic.png" alt="My Image"><p>After</p>'
        result = _sanitize_card_content(html)
        assert "<img" not in result
        assert "media-placeholder" in result
        assert "My Image" in result

    def test_img_no_alt_gets_default(self):
        html = '<img src="pic.png">'
        result = _sanitize_card_content(html)
        assert "Image" in result
        assert "media-placeholder" in result

    def test_video_replaced_with_placeholder(self):
        html = '<video controls><source src="v.mp4"></video>'
        result = _sanitize_card_content(html)
        assert "<video" not in result
        assert "media-placeholder" in result
        assert "Video" in result

    def test_iframe_replaced_with_placeholder(self):
        html = '<iframe title="Demo" src="https://x.com/w"></iframe>'
        result = _sanitize_card_content(html)
        assert "<iframe" not in result
        assert "media-placeholder" in result
        assert "Demo" in result

    def test_markdown_fallback(self):
        """Content without HTML tags triggers markdown conversion."""
        md = "## Heading\n\nSome **bold** text."
        result = _sanitize_card_content(md)
        assert "<h2>" in result
        assert "<strong>" in result

    def test_empty_content(self):
        result = _sanitize_card_content("")
        assert "No content" in result

    def test_mixed_media_and_text(self):
        """All three media types replaced, text preserved."""
        result = _sanitize_card_content(SAMPLE_CARD_WITH_MEDIA["content"])
        assert "media-placeholder" in result
        assert "Portal Screenshot" in result
        assert "<img" not in result
        assert "<video" not in result
        assert "<iframe" not in result
        assert "Follow these steps" in result


# ── Media Placeholder ────────────────────────────────────────────

class TestMediaPlaceholder:
    def test_image_placeholder(self):
        result = _media_placeholder("image", "Screenshot")
        assert "media-placeholder" in result
        assert "Screenshot" in result

    def test_video_placeholder(self):
        result = _media_placeholder("video", "Tutorial")
        assert "Video" in result
        assert "Tutorial" in result


# ── HTML to Markdown ─────────────────────────────────────────────

class TestHtmlToApproximateMd:
    def test_headings(self):
        html = "<h1>Title</h1><h2>Subtitle</h2>"
        md = _html_to_approximate_md(html)
        assert "# Title" in md
        assert "## Subtitle" in md

    def test_bold_and_italic(self):
        html = "<strong>bold</strong> and <em>italic</em>"
        md = _html_to_approximate_md(html)
        assert "**bold**" in md
        assert "*italic*" in md

    def test_links(self):
        html = '<a href="https://example.com">Click here</a>'
        md = _html_to_approximate_md(html)
        assert "[Click here](https://example.com)" in md

    def test_list_items(self):
        html = "<ul><li>Item one</li><li>Item two</li></ul>"
        md = _html_to_approximate_md(html)
        assert "- Item one" in md
        assert "- Item two" in md

    def test_empty(self):
        assert _html_to_approximate_md("") == ""

    def test_strips_unknown_tags(self):
        html = "<div><span>text</span></div>"
        md = _html_to_approximate_md(html)
        assert "text" in md
        assert "<" not in md


# ── Escape ───────────────────────────────────────────────────────

class TestEscape:
    def test_ampersand(self):
        assert "&amp;" in _escape("A & B")

    def test_angle_brackets(self):
        assert "&lt;" in _escape("<script>")
        assert "&gt;" in _escape("x > y")

    def test_quotes(self):
        assert "&quot;" in _escape('say "hello"')

    def test_none(self):
        assert _escape(None) == ""


# ── Gap Labels ───────────────────────────────────────────────────

class TestGapLabels:
    def test_all_labels_present(self):
        assert "critical" in GAP_LABELS
        assert "needs_update" in GAP_LABELS
        assert "minor" in GAP_LABELS
        assert "up_to_date" in GAP_LABELS

    def test_label_format(self):
        for key, (label, color) in GAP_LABELS.items():
            assert isinstance(label, str)
            assert label  # not empty
            assert color.startswith("#")


# ── guru_card_to_html() ─────────────────────────────────────────

class TestGuruCardToHtml:
    """Tests for the public guru_card_to_html() helper."""

    def test_basic_card_html(self):
        html = guru_card_to_html(SAMPLE_CARD)
        assert "<!DOCTYPE html>" in html
        assert "Billing Deduction Process" in html
        assert "Billing &amp; Payments" in html
        assert "guru-chrome" in html
        assert "guru-body" in html

    def test_card_with_redlines(self):
        html = guru_card_to_html(SAMPLE_CARD, SAMPLE_REDLINES)
        assert "redline-insert" in html
        assert "redline-modify" in html
        assert "redline-warning" in html
        assert "redline-legend" in html

    def test_card_without_redlines_no_legend(self):
        html = guru_card_to_html(SAMPLE_CARD)
        # Legend div should not appear in body (class exists in CSS, that's fine)
        body_html = html.split("</style>")[-1]
        assert '<div class="redline-legend"' not in body_html

    def test_verification_trusted(self):
        html = guru_card_to_html(SAMPLE_CARD)
        assert "Verified" in html

    def test_verification_needs(self):
        html = guru_card_to_html(SAMPLE_CARD_MARKDOWN)
        assert "Needs Verification" in html

    def test_media_card_placeholders(self):
        html = guru_card_to_html(SAMPLE_CARD_WITH_MEDIA)
        assert "media-placeholder" in html
        assert "Portal Screenshot" in html
        assert "<img" not in html.split("</style>")[-1]  # not in body

    def test_empty_card(self):
        html = guru_card_to_html({})
        assert "Untitled Card" in html

    def test_date_formatting(self):
        html = guru_card_to_html(SAMPLE_CARD)
        assert "Feb 14, 2026" in html

    def test_owner_in_meta(self):
        html = guru_card_to_html(SAMPLE_CARD)
        assert "CX Ops" in html

    def test_collection_badge(self):
        html = guru_card_to_html(SAMPLE_CARD)
        assert "guru-collection" in html

    def test_insert_redline_content(self):
        html = guru_card_to_html(SAMPLE_CARD, SAMPLE_REDLINES)
        assert "Step 3a (NEW)" in html

    def test_modify_redline_old_and_new(self):
        html = guru_card_to_html(SAMPLE_CARD, SAMPLE_REDLINES)
        assert "redline-old" in html
        assert "redline-new" in html
        assert "Billing Automation Team" in html

    def test_warning_redline(self):
        html = guru_card_to_html(SAMPLE_CARD, SAMPLE_REDLINES)
        assert "23 tickets" in html

    def test_markdown_content_converted(self):
        """Card with markdown content (no HTML tags) gets converted."""
        html = guru_card_to_html(SAMPLE_CARD_MARKDOWN)
        # Should contain converted heading
        assert "<h2>" in html.split("</style>")[-1]
