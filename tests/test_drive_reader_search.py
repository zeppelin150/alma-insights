"""DriveReader.search_files — Stage 2 of the Drive search funnel.

``search_files`` (src/data/drive_reader.py:209-220) is the only live-Drive
search entry point in the codebase, and it currently has ZERO coverage. It is
also effectively dead code in production: it is reached only through
``drive_query.query_business_drive``'s ``live_client`` seam, which no
production caller populates (see tests/test_drive_query.py).

This module pins its CONTRACT so a future fix is a deliberate, visible change:

* the ``q`` string and its degradation when the query is empty
* Shared-Drive support (corpora / includeItemsFromAllDrives / supportsAllDrives)
* ``_q()`` escaping — quotes, backslashes, and a query-injection attempt
* ``pageSize == limit``
* the PAGINATION GAP, as a named xfail (not a silent pass)

Style follows tests/test_drive_monitor.py:112-124: patch the built service with
a MagicMock and assert on the kwargs handed to ``files().list()``. No network,
no credentials, no Google libs required.

Assertions parse the ``q`` into clauses (split on ' and ') rather than matching
the whole string, so a benign reordering refactor does not break the suite.

MEASURE-ONLY: these tests document current behaviour, including its defects.
Do NOT "fix" drive_reader.py to make an xfail pass without the owner's say-so —
the eval harness exists to size these gaps before anyone repairs them.
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


def test_search_files_does_not_filter_to_a_folder():
    """search_files has no folder scoping — it searches the whole corpus.

    Contrast list_changed_files, which pins `<id> in parents`. An eval that
    points at one folder cannot use search_files to scope to it.
    """
    reader, svc = _reader_with_mock_service()
    reader.search_files("anything")
    assert "in parents" not in _list_kwargs(svc)["q"]


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
         "webViewLink": "https://drive/f1"},
    ]})
    out = reader.search_files("q")
    assert out == [{"name": "Doc One", "id": "f1",
                    "url": "https://drive/f1", "mime_type": "application/pdf"}]


# ── the pagination gap (DEFECT — visible, not silent) ──────────────────────

def test_search_files_makes_exactly_one_execute_call():
    """Documents the CURRENT single-shot behaviour.

    list_drives / list_folders / list_changed_files all loop on nextPageToken;
    search_files does not. This test passes today and will FAIL the moment
    someone adds paging — which is the signal to update the xfail below.
    """
    reader, svc = _reader_with_mock_service(
        {"files": [{"id": "a"}], "nextPageToken": "PAGE2"})
    reader.search_files("q", limit=1)
    assert svc.files().list().execute.call_count == 1


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT (measure-only, do not fix here): DriveReader.search_files "
    "(drive_reader.py:214-217) makes ONE files().list().execute() call and "
    "discards nextPageToken, unlike every sibling list_* method which loops. "
    "At limit=40 the 41st match is invisible, so recall@k is silently capped "
    "by the API page rather than by the ranking. Tracked by "
    "docs/DRIVE_SEARCH_EVAL.md."))
def test_search_files_follows_nextpagetoken():
    """The behaviour we WANT: exhaust pages until the result budget is met."""
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


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT (measure-only, do not fix here): no Drive READ path has rate "
    "limiting or 429/5xx backoff. gdrive_export._throttled_execute (0.5s "
    "sleep, 5 retries) guards WRITES only; DriveReader calls .execute() bare "
    "in all six read methods. A 100-doc index job can trip userRateLimitExceeded "
    "with no retry. Tracked by docs/DRIVE_SEARCH_EVAL.md."))
def test_search_files_retries_on_rate_limit():
    """The behaviour we WANT: a 429 is retried, not propagated."""
    reader = DriveReader("creds.json")
    svc = MagicMock()
    reader._service = svc
    svc.files().list().execute.side_effect = [
        Exception("HttpError 429 userRateLimitExceeded"),
        {"files": [{"id": "ok", "name": "recovered"}]},
    ]
    out = reader.search_files("q")
    assert out and out[0]["id"] == "ok"


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
