"""Deterministic document → Guru card conversion (pure Python, no LLM)."""

from src.data.doc_to_card import card_from_document, clean_name, title_from_name


class TestTitle:
    def test_strips_ext_and_separators(self):
        assert title_from_name("COB_Reduction_Pilot_PRD.docx") == \
            "COB Reduction Pilot PRD — Enablement Guide"

    def test_clean_name(self):
        assert clean_name("Q3-Pricing_Update.gdoc") == "Q3 Pricing Update"


class TestConversion:
    def test_lead_and_details(self):
        title, md = card_from_document(
            "Pilot.docx",
            "Providers must verify secondary coverage.\n\n"
            "Tier B moves to usage-based billing.")
        assert title.startswith("Pilot")
        assert "secondary coverage" in md      # lead
        assert "usage-based billing" in md      # details

    def test_date_becomes_callout(self):
        _, md = card_from_document(
            "x.txt", "Intro line.\n\nRollout: effective 2026-08-01.")
        assert "> [!NOTE]" in md and "2026-08-01" in md

    def test_markdown_passthrough(self):
        _, md = card_from_document("x.md", "Lead.\n\n## Steps\n\n- one\n- two")
        assert "## Steps" in md and "- one" in md

    def test_unicode_bullets_normalized(self):
        _, md = card_from_document("x.txt", "Lead.\n\n• alpha\n• beta")
        assert "- alpha" in md and "- beta" in md

    def test_allcaps_heading_inferred(self):
        _, md = card_from_document("x.txt", "Lead.\n\nOVERVIEW\n\nbody text")
        assert "## OVERVIEW" in md

    def test_empty_doc(self):
        title, md = card_from_document("empty.docx", "")
        assert "Enablement Guide" in title
        assert "no readable text" in md

    def test_faithful_no_invention(self):
        _, md = card_from_document("x.txt", "alpha beta gamma")
        assert "alpha beta gamma" in md
        # nothing beyond the source + minimal scaffolding
        assert "Update training material" not in md
