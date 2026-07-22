"""The bundled help corpus must SYNC, not just self-heal an empty table.

Field report 2026-07-22: the Help Center showed only 8 articles when the
bundled corpus has 62. Root cause: both load paths were presence-gated —
``help_tools._ensure_help_corpus`` returned early ``if count_articles > 0`` and
``HelpTab.reload`` loaded only ``if not articles`` — so a warehouse populated
back when only the 8 ``renn`` articles existed never picked up the 54 added
later. The loader is upsert-on-hash (its own docstring: "calling it on every
launch is cheap"), so the fix is to run it whenever the on-disk corpus has more
articles than the DB, not only when the DB is empty.

These tests seed a STALE subset (the exact field shape: a few renn rows) and
assert the full corpus is present after the load path runs.
"""

from __future__ import annotations

import pytest

from src.data.help import loader, store


def _disk_article_count() -> int:
    """How many parseable articles the bundled corpus actually has."""
    root = loader.help_dir()
    n = 0
    for path in root.rglob("*.md"):
        if loader.parse_article(path) is not None:
            n += 1
    return n


def _seed_stale_subset(conn):
    """Reproduce the exact field state: a warehouse first populated back when
    only the ``renn`` section existed, so it holds those (real) article rows and
    none of the sections authored later. Built by loading the full corpus then
    deleting every non-renn row — so the remaining rows are REAL corpus ids (no
    synthetic orphans), and a correct re-sync lands exactly the disk count."""
    loader.load_bundled_help(conn)
    conn.execute("DELETE FROM help_articles WHERE section <> 'renn'")
    conn.commit()


class TestEnsureHelpCorpusSyncsNewArticles:
    def test_ensure_loads_the_full_corpus_over_a_stale_subset(self, empty_db):
        """The data-layer path (help_search's _ensure_help_corpus). Pre-fix it
        returned True on the 3 seeded rows and never loaded the other ~59."""
        from src.data.chat_tools.help_tools import _ensure_corpus
        conn = empty_db.conn
        _seed_stale_subset(conn)
        seeded = store.count_articles(conn)
        assert 0 < seeded < _disk_article_count()   # only the renn subset

        assert _ensure_corpus(conn) is True
        assert store.count_articles(conn) == _disk_article_count()
        # sections authored after the initial renn-only load must now be present
        sections = {s["section"] for s in store.list_sections(conn)}
        assert {"troubleshooting", "workbench", "settings"} <= sections

    def test_ensure_still_loads_into_an_empty_table(self, empty_db):
        from src.data.chat_tools.help_tools import _ensure_corpus
        conn = empty_db.conn
        assert store.count_articles(conn) == 0
        assert _ensure_corpus(conn) is True
        assert store.count_articles(conn) == _disk_article_count()

    def test_ensure_is_cheap_when_already_current(self, empty_db, monkeypatch):
        """When the DB already matches disk, no upsert writes should fire — the
        sync must be a count check, not a full re-hash-and-write every call."""
        from src.data.chat_tools.help_tools import _ensure_corpus
        conn = empty_db.conn
        loader.load_bundled_help(conn)          # now current
        calls = {"n": 0}
        real = loader.load_bundled_help

        def counting(c, **kw):
            calls["n"] += 1
            return real(c, **kw)

        monkeypatch.setattr("src.data.chat_tools.help_tools.load_bundled_help",
                            counting, raising=False)
        # Already current → the ensure path must NOT re-run the full loader.
        _ensure_corpus(conn)
        assert calls["n"] == 0


class TestHelpTabReloadSyncsNewArticles:
    @pytest.mark.ui
    def test_reload_shows_the_full_corpus_over_a_stale_subset(self, empty_db):
        from PySide6.QtWidgets import QApplication
        from src.ui.pages.enablement.help_tab import HelpTab
        QApplication.instance() or QApplication([])
        conn = empty_db.conn
        _seed_stale_subset(conn)

        tab = HelpTab(lambda: conn)   # __init__ calls reload()
        assert store.count_articles(conn) == _disk_article_count()
        # ToC reflects the full corpus, not the 3 stale rows
        n_children = sum(tab._toc.topLevelItem(i).childCount()
                         for i in range(tab._toc.topLevelItemCount()))
        assert n_children == _disk_article_count()
