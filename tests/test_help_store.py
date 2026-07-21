"""Help corpus loader + store contract.

The corpus is authored as files and loaded into SQLite, so the load path is
the only thing standing between a typo in an article and a broken Help tab.
These tests pin: frontmatter parsing, the status vocabulary (the honesty
mechanism), id uniqueness, loader idempotency by content hash, and the FTS
triggers.
"""

import pytest

from src.data.help import loader, store


@pytest.fixture
def help_db(empty_db):
    return empty_db.conn


def _write(tmp_path, name, body, **meta):
    meta.setdefault("id", name)
    meta.setdefault("title", name.replace("-", " ").title())
    meta.setdefault("section", "test")
    fm = "\n".join(f"{k}: {v}" for k, v in meta.items())
    p = tmp_path / f"{name}.md"
    p.write_text(f"---\n{fm}\n---\n\n{body}\n", encoding="utf-8")
    return p


# ── the bundled corpus itself ─────────────────────────────────────────

def test_bundled_corpus_exists():
    assert loader.help_dir().exists(), "assets/help/ is missing"
    assert list(loader.help_dir().rglob("*.md")), "no help articles bundled"


def test_every_bundled_article_parses():
    bad = [p.name for p in loader.help_dir().rglob("*.md")
           if loader.parse_article(p) is None]
    assert not bad, f"unparseable help articles: {bad}"


def test_every_bundled_status_is_in_the_vocabulary():
    for path in loader.help_dir().rglob("*.md"):
        art = loader.parse_article(path)
        assert art["status"] in store.STATUSES, (
            f"{path.name} has status {art['status']!r}; the UI renders status "
            f"as a badge + banner, so an unknown value would silently present "
            f"a broken feature as working")


def test_every_bundled_id_is_unique():
    seen = {}
    for path in loader.help_dir().rglob("*.md"):
        art = loader.parse_article(path)
        assert art["article_id"] not in seen, (
            f"duplicate id {art['article_id']!r} in {path.name} and "
            f"{seen.get(art['article_id'])}")
        seen[art["article_id"]] = path.name


def test_every_bundled_article_has_a_summary():
    """The summary is what a search result shows — an empty one is a dead row."""
    missing = [p.name for p in loader.help_dir().rglob("*.md")
               if not (loader.parse_article(p) or {}).get("summary")]
    assert not missing, f"articles with no summary: {missing}"


def test_every_bundled_article_answers_all_three_questions():
    """The fixed template is the contract: how it works / how it should work /
    what to do when it doesn't."""
    for path in loader.help_dir().rglob("*.md"):
        body = loader.parse_article(path)["body"].lower()
        for heading in ("how it works", "how it should work", "if it doesn"):
            assert heading in body, f"{path.name} is missing '{heading}'"


def test_bundled_corpus_loads_into_the_db(help_db):
    summary = loader.load_bundled_help(help_db)
    assert summary["loaded"] > 0
    assert summary["errors"] == []
    assert store.count_articles(help_db) == summary["loaded"]


# ── loader behaviour ──────────────────────────────────────────────────

def test_loader_is_idempotent(help_db, tmp_path):
    _write(tmp_path, "a", "body one", summary="s")
    first = loader.load_bundled_help(help_db, directory=tmp_path)
    second = loader.load_bundled_help(help_db, directory=tmp_path)
    assert first["loaded"] == 1 and first["unchanged"] == 0
    assert second["loaded"] == 0 and second["unchanged"] == 1


def test_loader_rewrites_on_content_change(help_db, tmp_path):
    _write(tmp_path, "a", "body one", summary="s")
    loader.load_bundled_help(help_db, directory=tmp_path)
    _write(tmp_path, "a", "body two", summary="s")
    again = loader.load_bundled_help(help_db, directory=tmp_path)
    assert again["loaded"] == 1
    assert "body two" in store.get_article(help_db, "a")["body"]


def test_missing_frontmatter_is_skipped_not_fatal(help_db, tmp_path):
    (tmp_path / "broken.md").write_text("no frontmatter here", encoding="utf-8")
    _write(tmp_path, "good", "body", summary="s")
    summary = loader.load_bundled_help(help_db, directory=tmp_path)
    assert summary["loaded"] == 1 and summary["skipped"] == 1
    assert store.get_article(help_db, "good") is not None


def test_unknown_status_is_rejected(help_db, tmp_path):
    _write(tmp_path, "a", "body", summary="s", status="mostly-fine")
    summary = loader.load_bundled_help(help_db, directory=tmp_path)
    assert summary["loaded"] == 0 and summary["skipped"] == 1
    assert "mostly-fine" in " ".join(summary["errors"])


def test_duplicate_ids_are_skipped(help_db, tmp_path):
    _write(tmp_path, "one", "body", summary="s", id="dupe")
    _write(tmp_path, "two", "body", summary="s", id="dupe")
    summary = loader.load_bundled_help(help_db, directory=tmp_path)
    assert summary["loaded"] == 1 and summary["skipped"] == 1


def test_missing_directory_is_not_fatal(help_db, tmp_path):
    summary = loader.load_bundled_help(help_db, directory=tmp_path / "nope")
    assert summary == {"loaded": 0, "skipped": 0, "unchanged": 0, "errors": []}


# ── store queries ─────────────────────────────────────────────────────

def test_articles_come_back_in_reading_order(help_db, tmp_path):
    _write(tmp_path, "b", "x", summary="s", section_order=1, order=2)
    _write(tmp_path, "a", "x", summary="s", section_order=1, order=1)
    _write(tmp_path, "c", "x", summary="s", section_order=0, order=1)
    loader.load_bundled_help(help_db, directory=tmp_path)
    assert [a["article_id"] for a in store.list_articles(help_db)] == [
        "c", "a", "b"]


def test_sections_report_counts(help_db):
    loader.load_bundled_help(help_db)
    sections = store.list_sections(help_db)
    assert sections
    assert sum(s["n_articles"] for s in sections) == store.count_articles(help_db)


def test_status_banner_only_for_limited_features():
    assert store.status_banner("available") == ""
    for status in ("partial", "flag-gated", "not-available"):
        assert store.status_banner(status), f"{status} must carry a banner"


def test_article_carries_parsed_features_and_banner(help_db):
    loader.load_bundled_help(help_db)
    art = store.get_article(help_db, "renn-background-research")
    assert art is not None
    assert isinstance(art["features"], list)
    assert art["status"] == "not-available"
    assert art["banner"], "a not-available article must render a banner"


def test_applies_to_filters(help_db, tmp_path):
    _write(tmp_path, "e", "x", summary="s", applies_to="enablement")
    _write(tmp_path, "p", "x", summary="s", applies_to="product")
    _write(tmp_path, "b", "x", summary="s", applies_to="both")
    loader.load_bundled_help(help_db, directory=tmp_path)
    ids = {a["article_id"] for a in store.list_articles(help_db)}
    assert ids == {"e", "b"}


# ── FTS triggers ──────────────────────────────────────────────────────

def _fts_count(conn):
    return int(conn.execute(
        "SELECT COUNT(*) FROM help_articles_fts").fetchone()[0])


def test_fts_populated_on_insert(help_db, tmp_path):
    _write(tmp_path, "a", "body", summary="s")
    loader.load_bundled_help(help_db, directory=tmp_path)
    assert _fts_count(help_db) == 1


def test_fts_updated_not_duplicated_on_change(help_db, tmp_path):
    _write(tmp_path, "a", "original body", summary="s")
    loader.load_bundled_help(help_db, directory=tmp_path)
    _write(tmp_path, "a", "replacement body", summary="s")
    loader.load_bundled_help(help_db, directory=tmp_path)
    assert _fts_count(help_db) == 1, "the update trigger left a stale FTS row"
    hits = help_db.execute(
        "SELECT COUNT(*) FROM help_articles_fts "
        "WHERE help_articles_fts MATCH 'original'").fetchone()[0]
    assert hits == 0, "the old body is still searchable after an update"


def test_fts_cleared_on_delete(help_db, tmp_path):
    _write(tmp_path, "a", "body", summary="s")
    loader.load_bundled_help(help_db, directory=tmp_path)
    help_db.execute("DELETE FROM help_articles WHERE article_id = 'a'")
    help_db.commit()
    assert _fts_count(help_db) == 0
