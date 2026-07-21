"""In-app Help Center for the enablement side.

One corpus, two consumers. Articles are authored as markdown + YAML
frontmatter under ``assets/help/`` and loaded into ``help_articles`` /
``help_articles_fts``; the Help tab browses those rows and Renn's
``help_search`` tool retrieves them. The corpus is never forked per renderer.

Search is LEXICAL ONLY — FTS5 plus code-side ranking, no embeddings and no
LLM in the ranking path (owner-locked for the enablement lane).
"""

from src.data.help.store import (  # noqa: F401
    STATUSES, get_article, list_articles, list_sections, upsert_article,
)
from src.data.help.loader import load_bundled_help  # noqa: F401
from src.data.help.search import search_help  # noqa: F401
