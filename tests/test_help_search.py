"""Help search — lexical, tokenized, and specifically NOT LIKE-based.

The defining test here is `test_multiword_natural_question_finds_the_article`.
Six existing search paths in this repo wrap the entire query in a single
LIKE '%…%' (enablement_store.search_documents:94, search_drafts, kb/search.py's
fulltext floor, claude_tools, fast_path, ai_reports_history_tab), so a
multi-word question only matches when those words appear contiguously. Help
search is the one path users reach with a whole sentence, so it must not
inherit that flaw.
"""

import pytest

from src.data.help import loader, search, store


@pytest.fixture
def corpus(empty_db):
    conn = empty_db.conn
    summary = loader.load_bundled_help(conn)
    assert summary["loaded"] > 0, "the bundled corpus failed to load"
    return conn


# ── the flaw this module exists to avoid ──────────────────────────────

def test_multiword_natural_question_finds_the_article(corpus):
    """The exact shape a whole-query LIKE fails: words that are all present
    but not contiguous anywhere in the document."""
    hits = search.search_help(corpus, "how do I connect drive mid conversation")
    assert hits, "a multi-word natural question returned nothing"
    assert hits[0]["article_id"] == "renn-pickers"


def test_search_module_has_no_like_fallback():
    """A regression guard: reintroducing a LIKE floor would silently restore
    the multi-word failure this module was built to avoid."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "src" / "data" / "help" / "search.py").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in src.splitlines()
        if not line.strip().startswith("#"))
    code = code.split('"""')[0] + '"""'.join(code.split('"""')[2:])
    assert " LIKE " not in code.upper().replace("'", " ")


def test_scoring_uses_the_same_prefix_rule_as_retrieval(corpus):
    """Regression: the FTS MATCH prefix-matches ("publish"* hits "Publishing"),
    so the coverage and title-hit scoring must too. When they used exact
    equality the ranker discarded what FTS had found and the correct article
    fell out of the top five entirely."""
    hits = search.search_help(corpus, "why is drive publish not working",
                              limit=5)
    ids = [h["article_id"] for h in hits]
    assert "workbench-publish-drive" in ids, (
        f"the Drive-publish article is missing from {ids}")
    assert ids[0] == "workbench-publish-drive"


def test_stem_variants_reach_their_article(corpus):
    for query, expected in [
        ("publishing to guru", "workbench-publish-guru"),
        ("indexing a drive folder", "kb-bootstrapping"),
    ]:
        ids = [h["article_id"] for h in search.search_help(corpus, query,
                                                           limit=5)]
        assert expected in ids, f"{query!r} did not surface {expected}: {ids}"


@pytest.mark.parametrize("question", [
    "how do I reschedule a calendar task",
    "why does renn not write things itself",
    "what is on my plate today",
    "can renn do background research",
    "where do I find the assistant",
])
def test_whole_sentence_questions_return_something(corpus, question):
    assert search.search_help(corpus, question), f"no hit for {question!r}"


# ── ranking ───────────────────────────────────────────────────────────

def test_title_match_outranks_body_mention(corpus):
    hits = search.search_help(corpus, "confirm card")
    assert hits[0]["article_id"] == "renn-confirm-card"


def test_scores_are_ordered_descending(corpus):
    hits = search.search_help(corpus, "renn tools search")
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)


def test_limit_is_respected(corpus):
    assert len(search.search_help(corpus, "renn", limit=2)) <= 2


def test_section_filter(corpus):
    hits = search.search_help(corpus, "renn", section="renn")
    assert hits and all(h["section"] == "renn" for h in hits)
    assert search.search_help(corpus, "renn", section="nonexistent") == []


# ── the honesty contract ──────────────────────────────────────────────

def test_results_carry_status_so_renn_can_warn(corpus):
    hits = search.search_help(corpus, "background research")
    assert hits
    top = hits[0]
    assert top["article_id"] == "renn-background-research"
    assert top["status"] == "not-available"
    assert top["banner"], "a limited feature must carry banner text"


def test_every_result_carries_a_status(corpus):
    for hit in search.search_help(corpus, "renn", limit=10):
        assert hit["status"] in store.STATUSES


def test_results_carry_an_excerpt(corpus):
    for hit in search.search_help(corpus, "publish to guru"):
        assert hit["excerpt"], "a result with no excerpt is unreadable"


def test_body_is_not_returned_wholesale(corpus):
    """Search results feed a model prompt; shipping full bodies would blow the
    context for no benefit."""
    for hit in search.search_help(corpus, "renn"):
        assert "body" not in hit


# ── hostile and degenerate input ──────────────────────────────────────

@pytest.mark.parametrize("query", [
    "", "   ", None, "!!!", "***", "a", "-", "()",
])
def test_degenerate_queries_return_empty_not_raise(corpus, query):
    assert search.search_help(corpus, query) == []


@pytest.mark.parametrize("query", [
    'renn" OR 1=1 --',
    "renn AND (NEAR(x y))",
    'drive" NOT "asana',
    "*",
    "renn*)",
])
def test_fts_syntax_in_user_input_cannot_break_the_query(corpus, query):
    # Quoting neutralizes FTS operators; the call must return cleanly whatever
    # the user typed.
    result = search.search_help(corpus, query)
    assert isinstance(result, list)


def test_all_stopword_query_still_searches(corpus):
    """'what is this' is all stop words — dropping them all would match every
    article at once, so the fallback keeps the original tokens."""
    assert isinstance(search.search_help(corpus, "what is this"), list)


def test_very_long_query_is_capped(corpus):
    long_q = " ".join(f"term{i}" for i in range(200)) + " renn confirm card"
    assert isinstance(search.search_help(corpus, long_q), list)


def test_unicode_query_does_not_raise(corpus):
    for q in ("café", "日本語", "naïve résumé", "emoji 🎉 test"):
        assert isinstance(search.search_help(corpus, q), list)


# ── plural / singular folding ─────────────────────────────────────────

def test_plural_matches_singular(corpus):
    singular = search.search_help(corpus, "picker")
    plural = search.search_help(corpus, "pickers")
    assert singular and plural
    assert singular[0]["article_id"] == plural[0]["article_id"]
