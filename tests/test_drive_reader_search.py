"""DriveReader.search_files — Stage 2 of the Drive search funnel.

``search_files`` (src/data/drive_reader.py:209-220) is the only live-Drive
search entry point in the codebase, and it currently has ZERO coverage. It is
also effectively dead code in production: it is reached only through
``drive_query.query_business_drive``'s ``live_client`` seam, which no
production caller populates (see tests/test_drive_query.py).

This module pins its CONTRACT:

* the ``q`` string and its degradation when the query is empty
* Shared-Drive support (corpora / includeItemsFromAllDrives / supportsAllDrives)
* ``_q()`` escaping — quotes, backslashes, and a query-injection attempt
* optional folder scoping (``<id> in parents``)
* pagination (nextPageToken is followed to the caller's limit)
* retry/backoff on a transient 429

Style follows tests/test_drive_monitor.py:112-124: patch the built service with
a MagicMock and assert on the kwargs handed to ``files().list()``. No network,
no credentials, no Google libs required.

Assertions parse the ``q`` into clauses (split on ' and ') rather than matching
the whole string, so a benign reordering refactor does not break the suite.

NOTE: defects D2 (single-page) and D3 (no read retry) were repaired as part of
wiring live Google Drive search into Renn (owner-approved). The two tests that
were named xfails now assert the fixed behaviour.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.data.drive_reader import DriveReader, _q


# ── helpers ────────────────────────────────────────────────────────────────

def _reader_with_mock_service(list_response=None):
    """A DriveReader whose _build_service() is short-circuited to a MagicMock.

    Setting ``_service`` directly is what test_drive_monitor.py does — the
    cached-service early return in _build_service (drive_reader.py:65-66) means
    no auth, no googleapiclient import, no network.
    """
    reader = DriveReader("creds.json")
    svc = MagicMock()
    reader._service = svc
    svc.files().list().execute.return_value = (
        list_response if list_response is not None else {"files": []})
    return reader, svc


def _list_kwargs(svc) -> dict:
    """The kwargs of the LAST files().list(...) call that carried a q=."""
    for call in reversed(svc.files().list.call_args_list):
        if call.kwargs.get("q") is not None:
            return call.kwargs
    raise AssertionError("files().list() was never called with a q= argument")


def _clauses(q: str) -> list[str]:
    """Split a Drive q string into top-level ' and '-joined clauses."""
    return [c.strip() for c in q.split(" and ") if c.strip()]


# ── _q() escaping ──────────────────────────────────────────────────────────

def test_q_wraps_in_single_quotes():
    assert _q("folder1") == "'folder1'"


def test_q_escapes_single_quote():
    """A stray apostrophe must not terminate the quoted literal."""
    assert _q("O'Brien") == r"'O\'Brien'"


def test_q_escapes_backslash_before_quote():
    r"""Backslash must be escaped FIRST, else \' would be produced from a
    literal backslash and re-read as an escaped quote."""
    assert _q("a\\b") == r"'a\\b'"
    # the ordering trap: input ending in a backslash must not escape the
    # closing delimiter.
    assert _q("trailing\\") == r"'trailing\\'"


def test_search_files_injection_attempt_stays_one_clause():
    """`x' or name contains 'y` must NOT break out into extra clauses.

    Without _q()'s escaping this input would close the fullText literal and
    inject an `or` — which in Drive's grammar would also drop the
    `trashed = false` filter's effect by widening the match.
    """
    reader, svc = _reader_with_mock_service()
    reader.search_files("x' or name contains 'y")
    q = _list_kwargs(svc)["q"]

    # exactly two top-level clauses: the fullText term and trashed=false
    assert len(_clauses(q)) == 2, f"injection widened the query: {q!r}"
    # the whole payload — quotes and all — lives inside ONE escaped literal,
    # so the injected `or` is data, not grammar.
    assert q == (r"fullText contains 'x\' or name contains \'y' "
                 r"and trashed = false")
    # no unescaped quote exists anywhere except the two literal delimiters
    assert len([i for i, ch in enumerate(q)
                if ch == "'" and (i == 0 or q[i - 1] != "\\")]) == 2


# ── q construction ─────────────────────────────────────────────────────────

def test_search_files_builds_fulltext_and_trashed_clauses():
    reader, svc = _reader_with_mock_service()
    reader.search_files("prior authorization")
    cl = _clauses(_list_kwargs(svc)["q"])

    assert "fullText contains 'prior authorization'" in cl
    assert "trashed = false" in cl


def test_search_files_empty_query_degrades_to_trashed_only():
    """Empty query drops the fullText clause entirely — this returns
    EVERYTHING the account can see, capped only by pageSize. Worth knowing
    before wiring search_files to any user-supplied string."""
    reader, svc = _reader_with_mock_service()
    reader.search_files("")
    cl = _clauses(_list_kwargs(svc)["q"])

    assert cl == ["trashed = false"]
    assert "fullText" not in _list_kwargs(svc)["q"]


def test_search_files_no_folder_scope_by_default():
    """Without a folder_id, search_files searches the whole corpus (no
    `in parents` clause)."""
    reader, svc = _reader_with_mock_service()
    reader.search_files("anything")
    assert "in parents" not in _list_kwargs(svc)["q"]


def test_search_files_optional_folder_scope():
    """A folder_id scopes the search via `in parents`. With no subfolders under
    the root (this mock returns none), the search q carries the single familiar
    `<id> in parents` clause — the recursion machinery only widens it when the
    tree walk actually finds children (see TestRecursiveFolderScope)."""
    reader, svc = _reader_with_mock_service()
    reader.search_files("anything", folder_id="FOLDER-42")
    cl = _clauses(_list_kwargs(svc)["q"])
    assert "'FOLDER-42' in parents" in cl
    assert "fullText contains 'anything'" in cl
    assert "trashed = false" in cl


# ── recursive folder scope (2026-07-22) ────────────────────────────────────
#
# Drive's `in parents` is not recursive; the old scoped search returned 0 for
# every query against a corpus whose documents live in nested subfolders (the
# field trap: the operator picked the top folder, docs sat 4-5 levels down).
# search_files now enumerates the subtree first (list_subtree_folder_ids —
# level-by-level BFS with chunked parent-OR queries + a short TTL cache) and
# searches across the whole tree.


class _TreeService:
    """A fake Drive service backed by a folder tree + file rows.

    Folder-enumeration queries (mimeType folder) answer from ``tree``
    (parent id -> child folder ids); search queries return ``files`` rows
    whose 'parent' is in the q's parent set.
    """

    def __init__(self, tree, files):
        self.tree = tree
        self.file_rows = files
        self.enum_queries: list[str] = []
        self.search_queries: list[str] = []

    def files(self):
        return self

    def list(self, **kwargs):
        q = kwargs.get("q", "")
        svc = self

        class _Req:
            def execute(self):
                import re
                parents = re.findall(r"'([^']+)' in parents", q)
                if "mimeType = 'application/vnd.google-apps.folder'" in q:
                    svc.enum_queries.append(q)
                    kids = [k for p in parents for k in svc.tree.get(p, [])]
                    return {"files": [{"id": k} for k in kids]}
                svc.search_queries.append(q)
                rows = [f for f in svc.file_rows
                        if not parents or f.get("parent") in parents]
                return {"files": [{"id": f["id"], "name": f["name"]}
                                  for f in rows]}
        return _Req()


@pytest.fixture(autouse=True)
def _clear_subtree_cache():
    """The subtree memo is module-level state — never let one test's tree leak
    into another's."""
    from src.data import drive_reader as dr
    dr._SUBTREE_CACHE.clear()
    yield
    dr._SUBTREE_CACHE.clear()


def _tree_reader(tree, files):
    reader = DriveReader("creds.json")
    reader._service = _TreeService(tree, files)
    return reader, reader._service


class TestRecursiveFolderScope:
    def test_scoped_search_reaches_nested_subfolders(self):
        """The field trap: TOP's only child is a wrapper; docs live below it.
        The old direct-children scope returned 0 here."""
        tree = {"TOP": ["WRAP"], "WRAP": ["A", "B"], "A": ["A1"]}
        files = [{"id": "d1", "name": "deep doc", "parent": "A1"},
                 {"id": "d2", "name": "mid doc", "parent": "B"}]
        reader, svc = _tree_reader(tree, files)
        out = reader.search_files("anything", folder_id="TOP")
        assert {f["id"] for f in out} == {"d1", "d2"}
        searched = " ".join(svc.search_queries)
        for fid in ("TOP", "WRAP", "A", "B", "A1"):
            assert f"'{fid}' in parents" in searched

    def test_wide_trees_chunk_into_multiple_or_groups(self):
        from src.data.drive_reader import _PARENTS_PER_QUERY
        kids = [f"K{i}" for i in range(_PARENTS_PER_QUERY + 5)]
        reader, svc = _tree_reader({"TOP": kids}, [])
        reader.search_files("x", folder_id="TOP")
        assert len(svc.search_queries) == 2   # 21 folders -> 15 + 6

    def test_duplicate_hits_across_chunks_are_deduped(self):
        from src.data.drive_reader import _PARENTS_PER_QUERY
        kids = [f"K{i}" for i in range(_PARENTS_PER_QUERY + 1)]
        # the same file id matches in both chunks (multi-parent file)
        files = [{"id": "dup", "name": "n", "parent": "TOP"},
                 {"id": "dup", "name": "n", "parent": kids[-1]}]
        reader, svc = _tree_reader({"TOP": kids}, files)
        out = reader.search_files("x", folder_id="TOP")
        assert [f["id"] for f in out] == ["dup"]

    def test_multi_root_scope_unions_the_trees(self):
        tree = {"R1": ["C1"], "R2": []}
        files = [{"id": "a", "name": "n", "parent": "C1"},
                 {"id": "b", "name": "n", "parent": "R2"}]
        reader, svc = _tree_reader(tree, files)
        out = reader.search_files("x", folder_id=["R1", "R2"])
        assert {f["id"] for f in out} == {"a", "b"}

    def test_subtree_enumeration_is_cached_within_the_ttl(self):
        tree = {"TOP": ["A"], "A": []}
        reader, svc = _tree_reader(tree, [])
        reader.search_files("first", folder_id="TOP")
        first_enum = len(svc.enum_queries)
        reader.search_files("second", folder_id="TOP")
        assert len(svc.enum_queries) == first_enum, (
            "the second search within the TTL must reuse the memoized subtree")
        assert len(svc.search_queries) == 2

    def test_search_files_sets_last_scope_truncated(self):
        reader, svc = _tree_reader({"TOP": ["A"], "A": []}, [])
        reader.search_files("x", folder_id="TOP")
        assert reader._last_scope_truncated is False

    def test_max_folders_cap_bounds_the_walk_and_flags_truncation(self):
        # a 2-level bushy tree far over the cap
        kids = [f"K{i}" for i in range(40)]
        tree = {"TOP": kids}
        tree.update({k: [f"{k}-{j}" for j in range(10)] for k in kids})
        reader, svc = _tree_reader(tree, [])
        ids, truncated = reader.list_subtree_folder_ids("TOP", max_folders=25)
        assert len(ids) <= 25
        assert ids[0] == "TOP"
        assert truncated is True

    def test_exact_boundary_truncation_is_flagged(self):
        """The exact-cap-at-a-level-boundary case the old mid-level-only flag
        missed: TOP->[A,B] with cap 3 fills seen to exactly 3 (TOP,A,B) but A's
        child C is never expanded — must report truncated."""
        reader, svc = _tree_reader({"TOP": ["A", "B"], "A": ["C"]}, [])
        ids, truncated = reader.list_subtree_folder_ids("TOP", max_folders=3)
        assert ids == ["TOP", "A", "B"]
        assert truncated is True

    def test_untruncated_walk_reports_false(self):
        reader, svc = _tree_reader({"TOP": ["A"], "A": []}, [])
        ids, truncated = reader.list_subtree_folder_ids("TOP")
        assert set(ids) == {"TOP", "A"} and truncated is False

    def test_cache_key_includes_caps(self):
        """A tighter-capped walk must not poison a later default-capped call
        within the TTL (the caps are part of the cache key)."""
        kids = [f"K{i}" for i in range(20)]
        reader, svc = _tree_reader({"TOP": kids}, [])
        small, t1 = reader.list_subtree_folder_ids("TOP", max_folders=5)
        big, t2 = reader.list_subtree_folder_ids("TOP", max_folders=250)
        assert len(small) == 5 and t1 is True
        assert len(big) == 21 and t2 is False   # TOP + 20

    def test_deep_chunk_is_queried_even_when_shallow_fills_the_limit(self):
        """THE major review finding: with shallow folders full of matches and a
        small limit, the deepest chunk must STILL be queried (old code returned
        at the limit before ever sending the deep chunk's query)."""
        from src.data.drive_reader import _PARENTS_PER_QUERY
        # 2 chunks: 15 shallow folders + 1 deep folder holding the needle.
        shallow = [f"S{i}" for i in range(_PARENTS_PER_QUERY)]
        tree = {"TOP": shallow + ["DEEP"]}
        # every shallow folder has a matching doc; the needle is only in DEEP.
        files = [{"id": f"s{i}", "name": "match", "parent": shallow[i]}
                 for i in range(_PARENTS_PER_QUERY)]
        files.append({"id": "needle", "name": "match", "parent": "DEEP"})
        reader, svc = _tree_reader(tree, files)
        out = reader.search_files("match", limit=10, folder_id="TOP")
        # both chunks were searched (not just the first that filled the limit)
        assert len(svc.search_queries) == 2
        searched = " ".join(svc.search_queries)
        assert "'DEEP' in parents" in searched

    def test_empty_roots_return_empty_without_calls(self):
        reader, svc = _tree_reader({}, [])
        assert reader.list_subtree_folder_ids([]) == ([], False)
        assert reader.list_subtree_folder_ids("") == ([], False)
        assert svc.enum_queries == []


# ── Shared Drive support ───────────────────────────────────────────────────

@pytest.mark.parametrize("key,expected", [
    ("corpora", "allDrives"),
    ("includeItemsFromAllDrives", True),
    ("supportsAllDrives", True),
])
def test_search_files_supports_shared_drives(key, expected):
    """All three flags are required together — omitting any one silently
    restricts results to My Drive."""
    reader, svc = _reader_with_mock_service()
    reader.search_files("q")
    assert _list_kwargs(svc)[key] == expected


def test_search_files_pagesize_tracks_limit():
    reader, svc = _reader_with_mock_service()
    reader.search_files("q", limit=7)
    assert _list_kwargs(svc)["pageSize"] == 7


def test_search_files_maps_result_fields():
    reader, svc = _reader_with_mock_service({"files": [
        {"id": "f1", "name": "Doc One", "mimeType": "application/pdf",
         "webViewLink": "https://drive/f1", "modifiedTime": "2026-07-01T00:00:00Z"},
    ]})
    out = reader.search_files("q")
    assert out == [{"name": "Doc One", "id": "f1",
                    "url": "https://drive/f1", "mime_type": "application/pdf",
                    "modified": "2026-07-01T00:00:00Z"}]


# ── pagination + retry (D2 / D3, now fixed) ────────────────────────────────

def test_search_files_stops_paging_at_limit():
    """Respects the caller's limit — it does not fetch a further page once the
    budget is met, even when nextPageToken is present."""
    reader, svc = _reader_with_mock_service(
        {"files": [{"id": "a"}], "nextPageToken": "PAGE2"})
    out = reader.search_files("q", limit=1)
    assert [r["id"] for r in out] == ["a"]
    assert svc.files().list().execute.call_count == 1


def test_search_files_follows_nextpagetoken():
    """Exhausts pages until the result budget is met (D2 fixed)."""
    reader = DriveReader("creds.json")
    svc = MagicMock()
    reader._service = svc

    pages = [
        {"files": [{"id": "p1", "name": "one"}], "nextPageToken": "TOKEN2"},
        {"files": [{"id": "p2", "name": "two"}]},
    ]
    svc.files().list().execute.side_effect = list(pages)

    out = reader.search_files("q", limit=10)

    assert [r["id"] for r in out] == ["p1", "p2"], (
        "second page dropped — nextPageToken is ignored")


def test_search_files_retries_on_rate_limit():
    """A transient 429 is retried, not propagated (D3 fixed)."""
    reader = DriveReader("creds.json")
    svc = MagicMock()
    reader._service = svc
    svc.files().list().execute.side_effect = [
        Exception("HttpError 429 userRateLimitExceeded"),
        {"files": [{"id": "ok", "name": "recovered"}]},
    ]
    out = reader.search_files("q")
    assert out and out[0]["id"] == "ok"


def test_search_files_non_retryable_error_propagates():
    """A non-transient error (e.g. 404) is not retried — it surfaces at once."""
    reader = DriveReader("creds.json")
    svc = MagicMock()
    reader._service = svc
    svc.files().list().execute.side_effect = RuntimeError("HttpError 404 notFound")
    with pytest.raises(RuntimeError, match="404"):
        reader.search_files("q")


# ── _q() duplication ───────────────────────────────────────────────────────

def test_q_escaping_is_duplicated_inline_in_gdrive_export():
    """NOTE, not a defect gate: src/export/gdrive_export.py re-implements the
    same escape inline (lines 219-221 in find_child_by_app_property, and again
    around line 239 in test_connection) instead of importing _q.

    This test asserts the two implementations still AGREE, so a future fix to
    one that isn't mirrored in the other shows up here rather than as a
    production query-injection difference between the read and write clients.
    """
    inline = lambda v: str(v).replace("\\", "\\\\").replace("'", "\\'")  # noqa: E731
    for probe in ["plain", "O'Brien", "a\\b", "trailing\\", "x' or name contains 'y"]:
        assert _q(probe) == f"'{inline(probe)}'", (
            f"drive_reader._q and gdrive_export's inline escape diverged on {probe!r}")
