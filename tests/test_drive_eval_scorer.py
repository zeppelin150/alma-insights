"""Unit tests for the Drive-eval scorer (tests/drive_eval/scorer.py).

The scorer is the instrument the whole evaluation is read through — if it is
wrong, every conclusion about retrieval quality is wrong. These tests pin the
metric definitions against hand-computed values, not against the
implementation's own output.

Pure/offline: no network, no LLM, no Drive, no clock.
"""

from __future__ import annotations

import json

import pytest

from tests.drive_eval import scorer as SC
from tests.drive_eval.scorer import Query, RunResult


def _q(qid="q1", relevant=("needle",), category="cat", text="find the needle"):
    return Query(id=qid, query=text, category=category,
                 relevant={SC.normalize_key(r) for r in relevant})


def _run(qid="q1", retrieved=("needle",)):
    keys = [SC.normalize_key(r) for r in retrieved]
    return RunResult(query_id=qid, retrieved=keys, titles=list(retrieved))


# ── alias matching (gold names docs by TITLE, search returns doc_ids) ───────

def test_gold_by_title_matches_a_result_keyed_by_doc_id():
    """The realistic case: the owner writes titles in gold.yaml, but the search
    layer returns opaque ids. Without alias matching this scores 0.0 despite
    perfect retrieval."""
    q = _q(relevant=["Aetna Runbook 2026"])
    run = RunResult(query_id="q1", retrieved=["d_88"], titles=["Aetna Runbook 2026"],
                    aliases=[{"d_88", "aetna runbook 2026"}])
    row = SC.score_query(q, run)
    assert row["recall@1"] == 1.0 and row["rr"] == 1.0
    assert row["missed"] == []


def test_gold_by_doc_id_also_matches():
    q = _q(relevant=["d_88"])
    run = RunResult(query_id="q1", retrieved=["d_88"], titles=["Aetna Runbook 2026"],
                    aliases=[{"d_88", "aetna runbook 2026"}])
    assert SC.score_query(q, run)["recall@1"] == 1.0


def test_alias_fallback_uses_titles_when_aliases_absent():
    """RunResult.alias_at degrades to {retrieved, title} so callers that don't
    populate aliases still match on either."""
    q = _q(relevant=["Aetna Runbook 2026"])
    run = RunResult(query_id="q1", retrieved=["d_88"], titles=["Aetna Runbook 2026"])
    assert SC.score_query(q, run)["recall@1"] == 1.0


def test_aliases_do_not_create_false_positives():
    q = _q(relevant=["Aetna Runbook 2026"])
    run = RunResult(query_id="q1", retrieved=["d_99"], titles=["Aetna Runbook 2025"],
                    aliases=[{"d_99", "aetna runbook 2025"}])
    row = SC.score_query(q, run)
    assert row["recall@10"] == 0.0
    assert row["missed"] == ["aetna runbook 2026"]


# ── normalize_key ──────────────────────────────────────────────────────────

def test_normalize_key_casefolds_and_collapses_whitespace():
    assert SC.normalize_key("  Aetna   Prior  Auth ") == "aetna prior auth"
    assert SC.normalize_key("AETNA") == SC.normalize_key("aetna")


def test_normalize_key_keeps_punctuation():
    """Near-duplicate titles differing only by punctuation must stay distinct —
    the corpus is built around that ambiguity."""
    assert SC.normalize_key("Runbook (2026)") != SC.normalize_key("Runbook 2026")


# ── per-query metrics ──────────────────────────────────────────────────────

def test_perfect_hit_at_rank_1():
    row = SC.score_query(_q(), _run(retrieved=["needle", "noise"]))
    assert row["recall@1"] == 1.0
    assert row["rr"] == 1.0
    assert row["first_relevant_rank"] == 1
    assert row["r_precision"] == 1.0
    assert row["missed"] == []


def test_hit_at_rank_3_scores_recall_and_rr_correctly():
    row = SC.score_query(_q(), _run(retrieved=["a", "b", "needle", "c"]))
    assert row["recall@1"] == 0.0
    assert row["recall@3"] == 1.0
    assert row["rr"] == pytest.approx(1 / 3)
    assert row["first_relevant_rank"] == 3


def test_total_miss_scores_zero_not_none():
    row = SC.score_query(_q(), _run(retrieved=["a", "b", "c"]))
    assert row["recall@10"] == 0.0
    assert row["rr"] == 0.0
    assert row["first_relevant_rank"] is None
    assert row["missed"] == ["needle"]
    assert row["scored"] is True


def test_missing_run_is_a_total_miss_with_an_error_note():
    row = SC.score_query(_q(), None)
    assert row["rr"] == 0.0
    assert row["n_retrieved"] == 0
    assert row["error"] == "no results recorded"


def test_multi_relevant_recall_is_fractional():
    q = _q(relevant=["n1", "n2", "n3"])
    row = SC.score_query(q, _run(retrieved=["n1", "x", "n2"]))
    assert row["recall@3"] == pytest.approx(2 / 3)
    assert row["missed"] == ["n3"]
    # r_precision: R=3, top-3 contains 2 relevant
    assert row["r_precision"] == pytest.approx(2 / 3)


def test_precision_at_5_ceiling_for_single_needle():
    """Documented ceiling: one relevant doc caps precision@5 at 0.2."""
    row = SC.score_query(_q(), _run(retrieved=["needle", "a", "b", "c", "d"]))
    assert row["precision@5"] == pytest.approx(0.2)
    assert row["r_precision"] == 1.0        # the fairer single-needle number


def test_no_ground_truth_yields_none_not_zero():
    """A query with no gold must not be averaged in as a failure."""
    row = SC.score_query(_q(relevant=()), _run(retrieved=["a"]))
    assert row["scored"] is False
    assert row["recall@1"] is None and row["rr"] is None


# ── aggregation ────────────────────────────────────────────────────────────

def test_unscored_queries_excluded_from_averages():
    queries = [_q("q1", relevant=["needle"]), _q("q2", relevant=())]
    runs = [_run("q1", ["needle"]), _run("q2", ["whatever"])]
    rep = SC.score(queries, runs)

    assert rep["overall"]["n_queries"] == 2
    assert rep["overall"]["n_scored"] == 1
    assert rep["overall"]["recall@1"] == 1.0     # not dragged to 0.5


def test_mrr_is_the_mean_of_reciprocal_ranks():
    queries = [_q("q1"), _q("q2"), _q("q3")]
    runs = [_run("q1", ["needle"]),              # rr 1.0
            _run("q2", ["a", "needle"]),         # rr 0.5
            _run("q3", ["a", "b", "c"])]         # rr 0.0
    rep = SC.score(queries, runs)
    assert rep["overall"]["mrr"] == pytest.approx((1.0 + 0.5 + 0.0) / 3)
    assert rep["overall"]["n_total_miss"] == 1


def test_category_breakdown_groups_and_sorts():
    queries = [_q("q1", category="title-match"), _q("q2", category="buried-body"),
               _q("q3", category="buried-body")]
    runs = [_run("q1", ["needle"]), _run("q2", ["needle"]), _run("q3", ["x"])]
    rep = SC.score(queries, runs)

    assert list(rep["by_category"]) == ["buried-body", "title-match"]  # sorted
    assert rep["by_category"]["title-match"]["recall@1"] == 1.0
    assert rep["by_category"]["buried-body"]["recall@1"] == 0.5


# ── determinism ────────────────────────────────────────────────────────────

def test_report_is_byte_deterministic():
    queries = [_q("q2", category="b"), _q("q1", category="a")]
    runs = [_run("q1", ["needle"]), _run("q2", ["x", "needle"])]
    a = json.dumps(SC.score(queries, runs), sort_keys=True)
    b = json.dumps(SC.score(queries, runs), sort_keys=True)
    assert a == b
    assert "generated_at" not in json.loads(a), "no implicit clock in the report"


def test_generated_at_only_appears_when_supplied():
    rep = SC.score([_q()], [_run()], generated_at="2026-07-20T00:00:00Z")
    assert rep["generated_at"] == "2026-07-20T00:00:00Z"


# ── gold.yaml loading ──────────────────────────────────────────────────────

def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_load_gold_yaml_parses_entries(tmp_path):
    p = _write(tmp_path, "gold.yaml", """
queries:
  - id: q1
    query: aetna prior auth turnaround
    category: buried-body
    relevant: ["Aetna Prior Auth Runbook 2026"]
    notes: answer is in the body
  - id: q2
    query: returns window
    relevant: Returns Policy
""")
    qs = SC.load_gold_yaml(p)
    assert [q.id for q in qs] == ["q1", "q2"]
    assert qs[0].relevant == {"aetna prior auth runbook 2026"}
    assert qs[0].category == "buried-body"
    assert qs[1].category == "uncategorized"
    assert qs[1].relevant == {"returns policy"}     # bare string accepted


@pytest.mark.parametrize("body,msg", [
    ("queries: []", "non-empty"),
    ("something_else: 1", "non-empty"),
    ("queries:\n  - id: q1\n    query: ''\n", "no 'query' text"),
    ("queries:\n  - id: q1\n    query: a\n  - id: q1\n    query: b\n", "duplicate"),
])
def test_load_gold_yaml_rejects_malformed(tmp_path, body, msg):
    """A silently-empty gold set would score everything as a miss."""
    p = _write(tmp_path, "bad.yaml", body)
    with pytest.raises(ValueError, match=msg):
        SC.load_gold_yaml(p)


# ── judgments.csv round trip ───────────────────────────────────────────────

def test_load_judgments_csv_derives_gold_and_run(tmp_path):
    p = _write(tmp_path, "j.csv",
               "query_id,query,category,rank,doc_id,title,relevant\n"
               "q1,find the needle,buried-body,1,d_a,Doc A,0\n"
               "q1,,,2,d_b,Doc B,1\n"
               "q1,,,3,d_c,Doc C,\n")
    queries, runs = SC.load_judgments_csv(p)

    assert len(queries) == 1 and len(runs) == 1
    q, r = queries[0], runs[0]
    assert q.query == "find the needle"
    assert q.category == "buried-body"          # inherited from the first row
    assert q.relevant == {"d_b"}                # only the marked row
    assert r.retrieved == ["d_a", "d_b", "d_c"]

    row = SC.score_query(q, r)
    assert row["first_relevant_rank"] == 2
    assert row["rr"] == 0.5


def test_load_judgments_csv_orders_by_rank_not_file_order(tmp_path):
    p = _write(tmp_path, "j.csv",
               "query_id,rank,doc_id,relevant\n"
               "q1,3,d_c,0\nq1,1,d_a,1\nq1,2,d_b,0\n")
    _, runs = SC.load_judgments_csv(p)
    assert runs[0].retrieved == ["d_a", "d_b", "d_c"]


def test_load_judgments_csv_accepts_yes_and_true(tmp_path):
    p = _write(tmp_path, "j.csv",
               "query_id,rank,doc_id,relevant\n"
               "q1,1,d_a,yes\nq2,1,d_b,TRUE\nq3,1,d_c,no\n")
    queries, _ = SC.load_judgments_csv(p)
    rel = {q.id: q.relevant for q in queries}
    assert rel["q1"] == {"d_a"} and rel["q2"] == {"d_b"} and rel["q3"] == set()


def test_load_judgments_csv_requires_core_columns(tmp_path):
    p = _write(tmp_path, "j.csv", "query_id,title\nq1,Doc A\n")
    with pytest.raises(ValueError, match="missing column"):
        SC.load_judgments_csv(p)


def test_judged_with_nothing_relevant_is_not_confused_with_unjudged(tmp_path):
    """A query the owner judged where NOTHING was relevant is a confirmed
    retrieval failure. It must not vanish into 'no ground truth'."""
    p = _write(tmp_path, "j.csv",
               "query_id,rank,doc_id,relevant\n"
               "q_hit,1,d_a,1\n"
               "q_miss,1,d_x,0\nq_miss,2,d_y,0\n"
               "q_blank,1,d_z,\n")
    queries, runs = SC.load_judgments_csv(p)
    by_id = {q.id: q for q in queries}

    assert by_id["q_miss"].judged_but_nothing_relevant is True
    assert by_id["q_blank"].judged_but_nothing_relevant is False   # never judged
    assert by_id["q_hit"].judged_but_nothing_relevant is False     # found it

    rep = SC.score(queries, runs, ground_truth_mode="judgments")
    assert rep["overall"]["n_judged_no_relevant"] == 1
    # ...and it does NOT drag the scored averages down, because it has no gold
    assert rep["overall"]["n_scored"] == 1
    assert rep["overall"]["recall@1"] == 1.0


def test_markdown_warns_that_averages_flatter_when_judged_misses_exist(tmp_path):
    p = _write(tmp_path, "j.csv",
               "query_id,rank,doc_id,relevant\nq_hit,1,d_a,1\nq_miss,1,d_x,0\n")
    queries, runs = SC.load_judgments_csv(p)
    md = SC.to_markdown(SC.score(queries, runs, ground_truth_mode="judgments"))
    assert "FLATTER" in md
    assert "JUDGED: none relevant" in md


def test_judgments_mode_report_carries_the_upper_bound_caveat():
    rep = SC.score([_q()], [_run()], ground_truth_mode="judgments")
    assert "UPPER BOUND" in rep["caveat"]


def test_write_judgment_csv_round_trips_through_the_loader(tmp_path):
    queries = [_q("q1", relevant=["needle"], text="find the needle")]
    runs = [_run("q1", ["Alpha Doc", "needle", "Gamma Doc"])]
    out = SC.write_judgment_csv(queries, runs, tmp_path / "emit.csv")

    text = out.read_text("utf-8")
    assert text.splitlines()[0].endswith("relevant")
    assert text.count("\n") == 4                       # header + 3 results

    # blank judgments -> loader sees no relevant docs (nothing judged yet)
    loaded_q, loaded_r = SC.load_judgments_csv(out)
    assert loaded_r[0].retrieved == ["alpha doc", "needle", "gamma doc"]
    assert loaded_q[0].relevant == set()


def test_write_judgment_csv_emits_a_row_for_a_query_with_no_results(tmp_path):
    out = SC.write_judgment_csv([_q("q1")], [], tmp_path / "e.csv")
    assert "(no results)" in out.read_text("utf-8")


# ── markdown ───────────────────────────────────────────────────────────────

def test_markdown_contains_headline_metrics_and_per_query_rows():
    queries = [_q("q1", category="title-match"), _q("q2", category="buried-body")]
    runs = [_run("q1", ["needle"]), _run("q2", ["a", "b"])]
    md = SC.to_markdown(SC.score(queries, runs, run_label="local-mirror"))

    assert "# Drive search evaluation" in md
    assert "local-mirror" in md
    assert "recall@1" in md and "mrr" in md
    assert "## By category" in md
    assert "q1" in md and "q2" in md
    assert "r_precision normalizes" in md      # the ceiling caveat is stated


def test_write_report_emits_markdown_and_json(tmp_path):
    rep = SC.score([_q()], [_run()])
    md, js = SC.write_report(rep, tmp_path / "out" / "report.md")
    assert md.exists() and js.exists()
    assert js.name == "report.md.json"
    assert json.loads(js.read_text("utf-8"))["overall"]["recall@1"] == 1.0
