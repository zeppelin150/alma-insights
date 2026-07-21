"""Help Center tab — browse, search, and the status-banner honesty contract.

Offscreen Qt: assert through widget APIs, never screenshots (offscreen grabs
are always blank).
"""

import pytest
from PySide6.QtWidgets import QApplication

from src.data.help import loader
from src.ui.pages.enablement.help_tab import HelpTab

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def tab(qapp, empty_db):
    loader.load_bundled_help(empty_db.conn)
    return HelpTab(lambda: empty_db.conn)


def _all_children(tab):
    out = []
    for i in range(tab._toc.topLevelItemCount()):
        parent = tab._toc.topLevelItem(i)
        for j in range(parent.childCount()):
            out.append(parent.child(j))
    return out


# ── table of contents ─────────────────────────────────────────────────

def test_toc_lists_every_article(tab, empty_db):
    from src.data.help import store
    assert len(_all_children(tab)) == store.count_articles(empty_db.conn)


def test_toc_groups_by_section(tab):
    assert tab._toc.topLevelItemCount() >= 1
    titles = [tab._toc.topLevelItem(i).text(0)
              for i in range(tab._toc.topLevelItemCount())]
    assert "Renn, your assistant" in titles


def test_section_headers_are_not_selectable(tab):
    from PySide6.QtCore import Qt
    for i in range(tab._toc.topLevelItemCount()):
        parent = tab._toc.topLevelItem(i)
        assert not (parent.flags() & Qt.ItemIsSelectable)


def test_first_article_is_selected_on_load(tab):
    assert tab.current_article_id()


# ── rendering ─────────────────────────────────────────────────────────

def test_selecting_an_article_renders_its_title(tab):
    tab.show_article("renn-confirm-card")
    text = tab._viewer.toPlainText()
    assert "Why Renn never writes on its own" in text


def test_article_body_is_rendered_not_raw_frontmatter(tab):
    tab.show_article("renn-confirm-card")
    text = tab._viewer.toPlainText()
    assert "How it works" in text
    assert "section_order" not in text, "frontmatter leaked into the body"
    assert "---" not in text.split("How it works")[0][:200]


def test_unknown_article_id_is_a_no_op(tab):
    before = tab.current_article_id()
    tab.show_article("does-not-exist")
    assert tab.current_article_id() == before


# ── the honesty contract ──────────────────────────────────────────────

def test_not_available_article_shows_its_banner(tab):
    tab.show_article("renn-background-research")
    assert tab._banner.isVisibleTo(tab)
    assert "NOT AVAILABLE YET" in tab._banner.text()


def test_partial_article_shows_its_banner(tab):
    tab.show_article("renn-where-it-appears")
    assert tab._banner.isVisibleTo(tab)
    assert "PARTIAL" in tab._banner.text()


def test_available_article_shows_no_banner(tab):
    tab.show_article("renn-confirm-card")
    assert not tab._banner.isVisibleTo(tab)


def test_banner_clears_when_moving_to_an_available_article(tab):
    tab.show_article("renn-background-research")
    assert tab._banner.isVisibleTo(tab)
    tab.show_article("renn-confirm-card")
    assert not tab._banner.isVisibleTo(tab), (
        "a stale banner would mark a working feature as unavailable")


# ── search ────────────────────────────────────────────────────────────

def test_search_selects_the_matching_article(tab):
    tab._search.setText("how do I connect drive mid conversation")
    assert tab.current_article_id() == "renn-pickers"


def test_search_with_no_match_reports_rather_than_clearing(tab):
    before = tab.current_article_id()
    tab._search.setText("zzzzqqqq wwwwvvvv xxxxyyyy")
    assert "Nothing matched" in tab._status.text()
    assert tab.current_article_id() == before


def test_clearing_search_restores_the_count(tab):
    tab._search.setText("confirm card")
    tab._search.setText("")
    assert "articles" in tab._status.text()


# ── links ─────────────────────────────────────────────────────────────

def test_help_scheme_anchor_navigates_within_the_center(tab):
    from PySide6.QtCore import QUrl
    tab.show_article("renn-confirm-card")
    tab._on_anchor(QUrl("help://renn-pickers"))
    assert tab.current_article_id() == "renn-pickers"


def test_non_help_anchor_is_refused(tab):
    from PySide6.QtCore import QUrl
    tab.show_article("renn-confirm-card")
    before = tab.current_article_id()
    for hostile in ("https://example.com", "file:///etc/passwd",
                    "javascript:alert(1)"):
        tab._on_anchor(QUrl(hostile))
    assert tab.current_article_id() == before, (
        "a help page must not be able to navigate anywhere but the Help Center")


def test_viewer_does_not_open_links_itself(tab):
    assert tab._viewer.openLinks() is False
    assert tab._viewer.openExternalLinks() is False


# ── flag-a-bug signal ─────────────────────────────────────────────────

def test_flag_bug_emits_the_current_article(tab):
    seen = []
    tab.flag_bug_requested.connect(seen.append)
    tab.show_article("renn-pickers")
    tab._bug_btn.click()
    assert seen == ["renn-pickers"]


# ── degradation ───────────────────────────────────────────────────────

def test_tab_survives_a_dead_connection(qapp):
    t = HelpTab(lambda: None)
    assert "unavailable" in t._status.text().lower()


def test_tab_self_loads_an_empty_corpus(qapp, empty_db):
    """A fresh warehouse has no help rows; opening the tab should load the
    bundled corpus rather than show an empty shell."""
    t = HelpTab(lambda: empty_db.conn)
    assert len(_all_children(t)) > 0
