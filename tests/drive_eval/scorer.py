"""Deterministic ranking metrics for the Drive search evaluation.

Pure functions over plain data. NO network, NO LLM, NO clock, NO randomness —
the same inputs always produce byte-identical output, so two runs can be
diffed to attribute a change to the retrieval code rather than to the harness.

Ground truth arrives in either of two shapes, because the owner authors the
corpus and may not know the answers up front:

  A. ``gold.yaml`` — the owner declares, per query, which document(s) are
     relevant. Best when the needle is known by construction.

         queries:
           - id: q1
             query: "what is the prior auth turnaround for Aetna"
             category: buried-body
             relevant: ["Aetna Prior Auth Runbook 2026"]
             notes: "answer is in the body, not the title"

  B. ``judgments.csv`` — the harness runs the queries, emits every result in
     rank order, and the owner marks each row relevant or not. Ground truth is
     then DERIVED from the marks. Best for a corpus nobody has fully read.

         query_id,query,category,rank,doc_id,title,relevant
         q1,what is the prior auth...,buried-body,1,d_88,Aetna Runbook 2026,1
         q1,,,2,d_12,Aetna Runbook 2025,0

Mode B has a structural ceiling worth stating plainly: it can only judge what
the system RETRIEVED. A document that was never returned for any query is
invisible to it, so recall computed from judgments alone is an UPPER BOUND on
true recall. ``gold.yaml`` is the only way to catch a total miss. The report
labels which mode produced it (``ground_truth_mode``) for exactly this reason.

Metric definitions (stated because conventions differ):

  recall@k     |top-k ∩ relevant| / |relevant|.  With a single relevant doc
               this is the hit rate — "did we find it in the top k".
  precision@5  |top-5 ∩ relevant| / 5.  NOTE the ceiling: a query with one
               relevant doc can never exceed 0.2. Read it alongside
               r_precision, which normalizes by |relevant| and reaches 1.0.
  r_precision  |top-R ∩ relevant| / R, where R = |relevant|.
  MRR          mean of 1/rank of the FIRST relevant result (0 if none found).

Documents are matched by identity string. The runner supplies ``doc_id`` where
one exists and falls back to the document title; ``normalize_key`` applies
casefold + whitespace collapse so a hand-typed gold entry matches a
Drive-supplied title without the owner having to copy ids exactly.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# k values reported by default. Kept as a module constant so the markdown
# header and the JSON payload can never drift apart.
K_VALUES = (1, 3, 5, 10)
PRECISION_K = 5

_WS = re.compile(r"\s+")


def normalize_key(value: str) -> str:
    """Identity key for a document: casefolded, whitespace-collapsed.

    Lets ``"Aetna Prior Auth Runbook 2026"`` in a hand-written gold.yaml match
    ``"aetna prior auth  runbook 2026"`` coming back from Drive. Deliberately
    does NOT strip punctuation — ``Runbook (2026)`` and ``Runbook 2026`` are
    different documents in a corpus built around near-duplicate titles.
    """
    return _WS.sub(" ", str(value or "").strip()).casefold()


@dataclass
class Query:
    """One evaluation query plus its ground truth."""
    id: str
    query: str
    category: str = "uncategorized"
    relevant: set[str] = field(default_factory=set)   # normalized keys
    notes: str = ""
    # judgments mode only: True once ANY result row for this query carried a
    # non-blank relevant mark. Distinguishes "the owner looked and found
    # nothing relevant" (a real total miss) from "never judged" (no data).
    # Without this the two collapse into relevant==set() and a genuine miss
    # disappears from the averages.
    judged: bool = False

    @property
    def has_ground_truth(self) -> bool:
        return bool(self.relevant)

    @property
    def judged_but_nothing_relevant(self) -> bool:
        return self.judged and not self.relevant


@dataclass
class RunResult:
    """What the system returned for one query, in rank order (rank 1 first).

    ``aliases`` (optional, parallel to ``retrieved``) holds every identity a
    result may legitimately be named by — typically {doc_id, title}. Ground
    truth matches if it equals ANY alias.

    This exists because the two ground-truth modes name documents differently:
    a hand-written gold.yaml uses TITLES (the owner reads them in Drive), while
    the search layers return opaque ``doc_id``/``card_id`` values. Matching on
    the primary key alone silently scores every query as a total miss even when
    retrieval worked perfectly.
    """
    query_id: str
    retrieved: list[str] = field(default_factory=list)   # normalized primary keys
    titles: list[str] = field(default_factory=list)      # display, same order
    aliases: list[set[str]] = field(default_factory=list)
    error: str = ""

    def alias_at(self, i: int) -> set[str]:
        """All normalized identities of the i-th result."""
        if i < len(self.aliases) and self.aliases[i]:
            return self.aliases[i]
        keys = set()
        if i < len(self.retrieved):
            keys.add(self.retrieved[i])
        if i < len(self.titles):
            keys.add(normalize_key(self.titles[i]))
        return {k for k in keys if k}


# ── loading ────────────────────────────────────────────────────────────────

def load_gold_yaml(path: str | Path) -> list[Query]:
    """Parse a gold.yaml into Query objects. Raises on a malformed file —
    a silently-empty gold set would score every query as a miss and look like
    a retrieval catastrophe."""
    import yaml

    raw = yaml.safe_load(Path(path).read_text("utf-8")) or {}
    entries = raw.get("queries")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path}: expected a non-empty top-level 'queries' list")

    out: list[Query] = []
    seen: set[str] = set()
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            raise ValueError(f"{path}: queries[{i}] is not a mapping")
        qid = str(e.get("id") or f"q{i + 1}")
        if qid in seen:
            raise ValueError(f"{path}: duplicate query id {qid!r}")
        seen.add(qid)
        text = str(e.get("query") or "").strip()
        if not text:
            raise ValueError(f"{path}: queries[{i}] ({qid}) has no 'query' text")
        rel = e.get("relevant") or []
        if isinstance(rel, str):
            rel = [rel]
        out.append(Query(
            id=qid, query=text,
            category=str(e.get("category") or "uncategorized"),
            relevant={normalize_key(r) for r in rel if str(r).strip()},
            notes=str(e.get("notes") or ""),
        ))
    return out


def load_judgments_csv(path: str | Path) -> tuple[list[Query], list[RunResult]]:
    """Parse a judged CSV into (queries, runs).

    Ground truth is derived: a row marked relevant contributes its doc to that
    query's relevant set. The run is reconstructed from the rank column, so the
    CSV is self-contained — it carries both what was returned and what counted.

    A blank ``relevant`` cell means UNJUDGED and is counted as not-relevant for
    metric purposes, but tallied separately in ``unjudged`` so low judging
    coverage is visible rather than masquerading as poor retrieval.
    """
    rows = list(csv.DictReader(Path(path).read_text("utf-8-sig").splitlines()))
    if not rows:
        raise ValueError(f"{path}: no data rows")

    required = {"query_id", "rank", "doc_id"}
    missing = required - set(rows[0].keys() or [])
    if missing:
        raise ValueError(f"{path}: missing column(s): {', '.join(sorted(missing))}")

    queries: dict[str, Query] = {}
    ranked: dict[str, list[tuple[int, str, str]]] = {}
    unjudged = 0

    for r in rows:
        qid = (r.get("query_id") or "").strip()
        if not qid:
            continue
        q = queries.get(qid)
        if q is None:
            q = queries[qid] = Query(id=qid, query=(r.get("query") or "").strip(),
                                     category="uncategorized")
        # query text / category may appear only on the first row of a block
        if not q.query and (r.get("query") or "").strip():
            q.query = r["query"].strip()
        cat = (r.get("category") or "").strip()
        if cat and q.category == "uncategorized":
            q.category = cat

        doc = (r.get("doc_id") or "").strip() or (r.get("title") or "").strip()
        if not doc:
            continue
        key = normalize_key(doc)
        try:
            rank = int(str(r.get("rank") or "0").strip())
        except ValueError:
            rank = 0
        ranked.setdefault(qid, []).append((rank, key, (r.get("title") or doc).strip()))

        mark = (r.get("relevant") or "").strip().lower()
        if mark in ("1", "y", "yes", "true", "t"):
            q.relevant.add(key)
            q.judged = True
        elif mark in ("", "?", "unjudged"):
            unjudged += 1
        else:
            q.judged = True          # an explicit 0/no/false IS a judgment

    runs: list[RunResult] = []
    for qid, items in ranked.items():
        # stable: sort by declared rank, ties broken by original file order
        ordered = sorted(enumerate(items), key=lambda p: (p[1][0], p[0]))
        runs.append(RunResult(
            query_id=qid,
            retrieved=[k for _, (_, k, _) in ordered],
            titles=[t for _, (_, _, t) in ordered],
            # a gold entry may name the doc by id OR by title
            aliases=[{k for k in (key, normalize_key(title)) if k}
                     for _, (_, key, title) in ordered]))

    out_q = [queries[k] for k in sorted(queries)]
    out_r = sorted(runs, key=lambda r: r.query_id)
    for q in out_q:
        q.notes = (q.notes + (f" [{unjudged} unjudged rows in file]"
                              if unjudged and q is out_q[0] else "")).strip()
    return out_q, out_r


# ── metrics ────────────────────────────────────────────────────────────────

def _hits_within(run: RunResult | None, k: int, relevant: set[str]) -> set[str]:
    """Which relevant docs appear in the top-k, alias-aware."""
    if run is None:
        return set()
    found: set[str] = set()
    for i in range(min(k, len(run.retrieved))):
        found |= (run.alias_at(i) & relevant)
    return found


def _first_relevant_rank(run: RunResult | None, relevant: set[str]) -> int:
    if run is None:
        return 0
    for i in range(len(run.retrieved)):
        if run.alias_at(i) & relevant:
            return i + 1
    return 0


def score_query(q: Query, run: RunResult | None,
                k_values: tuple[int, ...] = K_VALUES) -> dict:
    """Metrics for a single query. A missing run scores as a total miss."""
    retrieved = list(run.retrieved) if run else []
    rel = q.relevant
    n_rel = len(rel)

    row: dict = {
        "query_id": q.id, "query": q.query, "category": q.category,
        "n_relevant": n_rel, "n_retrieved": len(retrieved),
        "error": (run.error if run else "no results recorded"),
        "notes": q.notes,
        "judged_no_relevant": q.judged_but_nothing_relevant,
    }
    if not n_rel:
        # No ground truth -> metrics are undefined, not zero. Saying 0.0 here
        # would drag the corpus average down and look like a retrieval failure.
        row.update({f"recall@{k}": None for k in k_values})
        row.update({f"precision@{PRECISION_K}": None, "r_precision": None,
                    "rr": None, "scored": False,
                    "first_relevant_rank": None})
        return row

    for k in k_values:
        row[f"recall@{k}"] = len(_hits_within(run, k, rel)) / n_rel

    row[f"precision@{PRECISION_K}"] = \
        len(_hits_within(run, PRECISION_K, rel)) / PRECISION_K
    row["r_precision"] = len(_hits_within(run, n_rel, rel)) / n_rel

    fr = _first_relevant_rank(run, rel)
    row["first_relevant_rank"] = fr or None
    row["rr"] = (1.0 / fr) if fr else 0.0
    row["scored"] = True
    # which relevant docs were never returned at all — the actionable column
    row["missed"] = sorted(rel - _hits_within(run, len(retrieved), rel))
    return row


def _mean(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return (sum(vals) / len(vals)) if vals else None


def _aggregate(rows: list[dict], k_values: tuple[int, ...]) -> dict:
    scored = [r for r in rows if r.get("scored")]
    agg: dict = {"n_queries": len(rows), "n_scored": len(scored),
                 "n_unscored": len(rows) - len(scored)}
    for k in k_values:
        agg[f"recall@{k}"] = _mean([r[f"recall@{k}"] for r in scored])
    agg[f"precision@{PRECISION_K}"] = _mean(
        [r[f"precision@{PRECISION_K}"] for r in scored])
    agg["r_precision"] = _mean([r["r_precision"] for r in scored])
    agg["mrr"] = _mean([r["rr"] for r in scored])
    agg["n_total_miss"] = sum(1 for r in scored if not r.get("first_relevant_rank"))
    # Judged-but-nothing-relevant queries are unscorable (no gold to measure
    # against) yet are NOT "no data" — they are confirmed retrieval failures.
    # Counted separately so they cannot hide inside n_unscored.
    agg["n_judged_no_relevant"] = sum(
        1 for r in rows if r.get("judged_no_relevant"))
    return agg


def score(queries: list[Query], runs: list[RunResult], *,
          k_values: tuple[int, ...] = K_VALUES,
          ground_truth_mode: str = "gold",
          run_label: str = "",
          generated_at: str | None = None) -> dict:
    """Full report. ``generated_at`` is caller-supplied and OMITTED by default
    so the output stays byte-deterministic and diffable."""
    by_id = {r.query_id: r for r in runs}
    rows = [score_query(q, by_id.get(q.id), k_values) for q in queries]

    categories: dict[str, list[dict]] = {}
    for r in rows:
        categories.setdefault(r["category"], []).append(r)

    report = {
        "ground_truth_mode": ground_truth_mode,
        "run_label": run_label,
        "k_values": list(k_values),
        "overall": _aggregate(rows, k_values),
        "by_category": {cat: _aggregate(categories[cat], k_values)
                        for cat in sorted(categories)},
        "queries": rows,
    }
    if generated_at:
        report["generated_at"] = generated_at
    if ground_truth_mode == "judgments":
        report["caveat"] = (
            "Ground truth derived from judged results only. A document never "
            "retrieved by any query cannot be judged, so recall here is an "
            "UPPER BOUND on true recall. Use gold.yaml to detect total misses.")
    return report


# ── rendering ──────────────────────────────────────────────────────────────

def _fmt(v, nd: int = 3) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def to_markdown(report: dict) -> str:
    ks = tuple(report["k_values"])
    ov = report["overall"]
    lines: list[str] = ["# Drive search evaluation", ""]
    if report.get("run_label"):
        lines.append(f"**Run:** {report['run_label']}  ")
    if report.get("generated_at"):
        lines.append(f"**Generated:** {report['generated_at']}  ")
    lines += [f"**Ground truth:** {report['ground_truth_mode']}  ",
              f"**Queries:** {ov['n_queries']} "
              f"({ov['n_scored']} scored, {ov['n_unscored']} without ground truth)",
              ""]
    if report.get("caveat"):
        lines += [f"> {report['caveat']}", ""]
    if ov.get("n_judged_no_relevant"):
        lines += [f"> **{ov['n_judged_no_relevant']} quer(y/ies) were judged and "
                  "had NOTHING relevant in the results.** These are confirmed "
                  "retrieval failures, but they carry no gold document so they "
                  "cannot be scored — they are excluded from the averages above, "
                  "which therefore FLATTER the system. Supply the expected "
                  "document in a gold.yaml to score them.", ""]

    metric_cols = [f"recall@{k}" for k in ks] + \
                  [f"precision@{PRECISION_K}", "r_precision", "mrr"]

    lines += ["## Overall", "",
              "| " + " | ".join(metric_cols) + " | total misses |",
              "|" + "|".join(["---"] * (len(metric_cols) + 1)) + "|",
              "| " + " | ".join(_fmt(ov.get(c)) for c in metric_cols) +
              f" | {ov['n_total_miss']} |", "",
              f"_precision@{PRECISION_K} is capped at "
              f"|relevant|/{PRECISION_K} per query; r_precision normalizes for "
              "that and is the fairer single-needle number._", ""]

    if len(report["by_category"]) > 1:
        lines += ["## By category", "",
                  "| category | n | " + " | ".join(metric_cols) + " |",
                  "|" + "|".join(["---"] * (len(metric_cols) + 2)) + "|"]
        for cat in sorted(report["by_category"]):
            a = report["by_category"][cat]
            lines.append(f"| {cat} | {a['n_scored']}/{a['n_queries']} | " +
                         " | ".join(_fmt(a.get(c)) for c in metric_cols) + " |")
        lines.append("")

    lines += ["## Per query", "",
              "| id | category | query | rel | ret | first hit | "
              f"recall@{ks[-1]} | rr | missed |",
              "|" + "|".join(["---"] * 9) + "|"]
    for r in report["queries"]:
        missed = ", ".join(r.get("missed") or []) or (
            "—" if r.get("scored")
            else "JUDGED: none relevant" if r.get("judged_no_relevant")
            else "no gold")
        if len(missed) > 60:
            missed = missed[:57] + "…"
        q_text = r["query"] if len(r["query"]) <= 48 else r["query"][:45] + "…"
        lines.append(
            f"| {r['query_id']} | {r['category']} | {q_text} | {r['n_relevant']} | "
            f"{r['n_retrieved']} | {_fmt(r.get('first_relevant_rank'))} | "
            f"{_fmt(r.get(f'recall@{ks[-1]}'))} | {_fmt(r.get('rr'))} | {missed} |")
    lines.append("")

    errs = [r for r in report["queries"] if r.get("error")]
    if errs:
        lines += ["## Errors", ""]
        lines += [f"- `{r['query_id']}`: {r['error']}" for r in errs]
        lines.append("")
    return "\n".join(lines)


def write_report(report: dict, path: str | Path) -> tuple[Path, Path]:
    """Write ``<path>`` (markdown) and ``<path>.json``. Returns both paths."""
    md_path = Path(path)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path = md_path.with_suffix(md_path.suffix + ".json")
    md_path.write_text(to_markdown(report), encoding="utf-8")
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True,
                                    ensure_ascii=False), encoding="utf-8")
    return md_path, json_path


def write_judgment_csv(queries: list[Query], runs: list[RunResult],
                       path: str | Path) -> Path:
    """Emit results in rank order with a blank ``relevant`` column for the
    owner to fill in — the mode-B round trip."""
    by_id = {r.query_id: r for r in runs}
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["query_id", "query", "category", "rank", "doc_id",
                    "title", "relevant"])
        for q in queries:
            run = by_id.get(q.id)
            if not run or not run.retrieved:
                w.writerow([q.id, q.query, q.category, 0, "", "(no results)", ""])
                continue
            for i, (key, title) in enumerate(zip(run.retrieved, run.titles), start=1):
                w.writerow([q.id, q.query if i == 1 else "",
                            q.category if i == 1 else "", i, key, title, ""])
    return out
