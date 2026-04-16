"""Post-NLP processing — enriched trends and ticket-theme tagging."""

from src.data.post_nlp.enriched_trends import compute_enriched_trends
from src.data.post_nlp.ticket_theme_tagger import tag_tickets_to_findings

__all__ = ["compute_enriched_trends", "tag_tickets_to_findings"]
