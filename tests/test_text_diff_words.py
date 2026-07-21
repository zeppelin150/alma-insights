"""Word-level diff (text_diff.diff_words) — the web Workbench's review view.

Invariants: row order/tags match diff_rows; concatenating a row's span texts
reproduces the row verbatim; only genuinely-changed words are marked.
"""

import pytest

from src.data.text_diff import diff_rows, diff_words


def _texts(spans):
    return "".join(s["text"] for s in spans)


def test_rows_and_tags_match_diff_rows():
    old = "# Title\nkeep this line\nthe rollout is June 10\nremove me"
    new = "# Title\nkeep this line\nthe rollout is June 24\nadded line"
    plain = diff_rows(old, new)
    worded = diff_words(old, new)
    assert [(r["tag"], r["text"]) for r in worded] == \
           [(r["tag"], r["text"]) for r in plain]


def test_single_word_change_marks_only_that_word():
    rows = diff_words("the rollout is June 10", "the rollout is June 24")
    del_row = next(r for r in rows if r["tag"] == "del")
    add_row = next(r for r in rows if r["tag"] == "add")
    assert [s["text"] for s in del_row["spans"] if s["tag"] == "del"] == ["10"]
    assert [s["text"] for s in add_row["spans"] if s["tag"] == "add"] == ["24"]
    # equal words shared, spans reconstruct the lines exactly
    assert _texts(del_row["spans"]) == "the rollout is June 10"
    assert _texts(add_row["spans"]) == "the rollout is June 24"


def test_pure_insert_and_delete_are_whole_line_spans():
    rows = diff_words("a\nb", "a\nb\nc")
    add = next(r for r in rows if r["tag"] == "add")
    assert add["spans"] == [{"tag": "add", "text": "c"}]
    rows = diff_words("a\nb\nc", "a\nc")
    dele = next(r for r in rows if r["tag"] == "del")
    assert dele["spans"] == [{"tag": "del", "text": "b"}]


def test_unbalanced_replace_pairs_positionally():
    rows = diff_words("one two three\nsecond old", "one 2 three")
    dels = [r for r in rows if r["tag"] == "del"]
    adds = [r for r in rows if r["tag"] == "add"]
    assert len(dels) == 2 and len(adds) == 1
    # first del pairs with the add (word-level); the unpaired one is whole-line
    assert any(s["tag"] == "equal" for s in dels[0]["spans"])
    assert dels[1]["spans"] == [{"tag": "del", "text": "second old"}]


def test_equal_rows_carry_no_spans():
    rows = diff_words("same\nchanged a", "same\nchanged b")
    eq = next(r for r in rows if r["tag"] == "equal")
    assert "spans" not in eq


def test_whitespace_preserved_in_spans():
    rows = diff_words("a  b   c", "a  x   c")
    del_row = next(r for r in rows if r["tag"] == "del")
    assert _texts(del_row["spans"]) == "a  b   c"


def test_empty_inputs():
    assert diff_words("", "") == []
    rows = diff_words("", "new")
    assert rows == [{"tag": "add", "text": "new",
                     "spans": [{"tag": "add", "text": "new"}]}]


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
