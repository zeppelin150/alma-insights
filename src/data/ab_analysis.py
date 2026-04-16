"""
Alma Insights — A/B Dataset Comparison Engine (Pass 3.0)
Statistical comparison of two ticket datasets using chi-squared, Mann-Whitney U,
and independent t-tests via scipy.stats.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime

import numpy as np
from scipy import stats as sp_stats
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer


def compute_dataset_stats(db: object, dataset_id: int | None, date_start: str = "", date_end: str = "") -> dict:
    """Compute comprehensive statistics for a single dataset.

    Returns dict with keys: ticket_count, date_range, trc_distribution,
    tfidf_top30, sentiment_by_trc, csat_by_trc, resolution_times,
    volume_by_trc, nmf_topics.
    """
    conditions = []
    params = []

    if dataset_id is not None and dataset_id != 0:
        conditions.append("dataset_id = ?")
        params.append(dataset_id)
    if date_start:
        conditions.append("created_at >= ?")
        params.append(date_start)
    if date_end:
        conditions.append("created_at <= ?")
        params.append(date_end)

    where = " AND ".join(conditions) if conditions else "1=1"

    from src.data.source_registry import SourceRegistry
    from src.data.warehouse_query import WarehouseQuery
    _registry = SourceRegistry(db.conn)
    _wq = WarehouseQuery(db.conn, _registry)

    # ── Ticket count & date range ──
    _cnt_rows = _wq.query_conversations_raw(
        f"SELECT COUNT(*) as cnt, MIN(created_at) as min_d, MAX(created_at) as max_d "
        f"FROM {{table}} WHERE {where}", params
    )
    ticket_count = sum(r[0] for r in _cnt_rows if r[0])
    actual_start = min((r[1] for r in _cnt_rows if r[1]), default=None) or date_start or ""
    actual_end = max((r[2] for r in _cnt_rows if r[2]), default=None) or date_end or ""

    # ── TRC distribution ──
    _trc_rows = _wq.query_conversations_raw(f"""
        SELECT trc_code, COUNT(*) as cnt
        FROM {{table}} WHERE {where} AND trc_code != ''
        GROUP BY trc_code ORDER BY cnt DESC
    """, params)
    trc_distribution = {}
    for r in _trc_rows:
        trc_distribution[r[0]] = trc_distribution.get(r[0], 0) + r[1]

    # ── CSAT by TRC ──
    _csat_rows = _wq.query_conversations_raw(f"""
        SELECT trc_code, AVG(csat_score) as avg_csat,
               COUNT(csat_score) as rated
        FROM {{table}} WHERE {where} AND csat_score IS NOT NULL AND trc_code != ''
        GROUP BY trc_code
    """, params)
    csat_by_trc = {}
    for r in _csat_rows:
        if r[0] not in csat_by_trc:
            csat_by_trc[r[0]] = {"avg": round(r[1], 2) if r[1] else 0, "count": r[2]}
        else:
            old = csat_by_trc[r[0]]
            total_n = old["count"] + r[2]
            csat_by_trc[r[0]] = {
                "avg": round((old["avg"] * old["count"] + (r[1] or 0) * r[2]) / total_n, 2) if total_n else 0,
                "count": total_n,
            }

    # ── Resolution times (JOIN per source) ──
    _c_where = where.replace('created_at', 'c.created_at').replace('dataset_id', 'c.dataset_id')
    res_rows_flat = []
    for src in _registry.list_sources():
        prefix = src["table_prefix"]
        try:
            _rr = db.conn.execute(f"""
                SELECT t.assignment_to_resolution_hours, t.total_resolution_hours, t.first_reply_hours
                FROM [{prefix}_tickets] t
                JOIN [{prefix}_conversations] c ON t.ticket_id = c.ticket_id
                WHERE {_c_where} AND t.assignment_to_resolution_hours IS NOT NULL
            """, params).fetchall()
            res_rows_flat.extend(_rr)
        except Exception:
            pass
    if not res_rows_flat and _wq._legacy_mode:
        res_rows_flat = db.conn.execute(f"""
            SELECT t.assignment_to_resolution_hours, t.total_resolution_hours, t.first_reply_hours
            FROM tickets t JOIN conversations c ON t.ticket_id = c.ticket_id
            WHERE {_c_where} AND t.assignment_to_resolution_hours IS NOT NULL
        """, params).fetchall()
    resolution_times = [r[1] for r in res_rows_flat if r[1] is not None]
    first_reply_times = [r[2] for r in res_rows_flat if r[2] is not None]

    # ── Sentiment by TRC (VADER) ──
    analyzer = SentimentIntensityAnalyzer()
    preview_rows = _wq.query_conversations_raw(f"""
        SELECT trc_code, thread_preview
        FROM {{table}} WHERE {where} AND thread_preview != '' AND trc_code != ''
    """, params)

    sentiment_by_trc = {}
    for r in preview_rows:
        trc = r[0]
        compound = analyzer.polarity_scores(r[1])["compound"]
        sentiment_by_trc.setdefault(trc, []).append(compound)

    # Average per TRC
    for trc in sentiment_by_trc:
        vals = sentiment_by_trc[trc]
        sentiment_by_trc[trc] = {
            "avg_compound": round(np.mean(vals), 4),
            "count": len(vals),
            "std": round(float(np.std(vals)), 4) if len(vals) > 1 else 0.0,
            "values": vals,  # Keep raw for Mann-Whitney
        }

    # ── TF-IDF top 30 ──
    tfidf_top30 = []
    try:
        from src.data.trending_engine import run_full_analysis
        from src.data.connection_factory import get_connection
        conn = get_connection(db.db_path)
        analysis = run_full_analysis(
            conn, actual_start[:10], actual_end[:10],
            trc_filter=None, window_size="Weekly",
            topic_method="nmf", db=db,
        )
        conn.close()
        terms_data = analysis.get("terms", {})
        all_terms = terms_data.get("all_terms", [])
        tfidf_top30 = all_terms[:30] if all_terms else []
    except Exception:
        pass

    return {
        "dataset_id": dataset_id,
        "ticket_count": ticket_count,
        "date_range": f"{actual_start[:10]} to {actual_end[:10]}",
        "date_start": actual_start[:10] if actual_start else "",
        "date_end": actual_end[:10] if actual_end else "",
        "trc_distribution": trc_distribution,
        "csat_by_trc": csat_by_trc,
        "sentiment_by_trc": sentiment_by_trc,
        "resolution_times": resolution_times,
        "first_reply_times": first_reply_times,
        "tfidf_top30": tfidf_top30,
    }


def compare_datasets(stats_a: dict, stats_b: dict) -> dict:
    """Run statistical comparisons between two dataset stats dicts.

    Returns dict with:
      - volume_chi2: chi-squared test on TRC volume distribution
      - sentiment_mwu: Mann-Whitney U per TRC on compound sentiment
      - csat_ttest: independent t-test on CSAT scores per TRC
      - resolution_ttest: t-test on resolution times
      - tfidf_rank_diff: top TF-IDF rank changes between datasets
    """
    comparison = {}

    # ── 1. Chi-squared on TRC volume ──
    all_trcs = sorted(set(list(stats_a["trc_distribution"].keys()) +
                          list(stats_b["trc_distribution"].keys())))
    if len(all_trcs) >= 2:
        observed_a = [stats_a["trc_distribution"].get(t, 0) for t in all_trcs]
        observed_b = [stats_b["trc_distribution"].get(t, 0) for t in all_trcs]

        # Create contingency table
        contingency = np.array([observed_a, observed_b])
        # Filter out columns with all zeros
        nonzero_cols = contingency.sum(axis=0) > 0
        contingency = contingency[:, nonzero_cols]
        filtered_trcs = [t for t, nz in zip(all_trcs, nonzero_cols) if nz]

        if contingency.shape[1] >= 2:
            chi2, p_val, dof, expected = sp_stats.chi2_contingency(contingency)
            # Per-TRC contributions
            trc_diffs = []
            for i, trc in enumerate(filtered_trcs):
                pct_a = contingency[0, i] / max(contingency[0].sum(), 1) * 100
                pct_b = contingency[1, i] / max(contingency[1].sum(), 1) * 100
                trc_diffs.append({
                    "trc": trc,
                    "count_a": int(contingency[0, i]),
                    "count_b": int(contingency[1, i]),
                    "pct_a": round(pct_a, 1),
                    "pct_b": round(pct_b, 1),
                    "pct_delta": round(pct_b - pct_a, 1),
                })
            # Sort by absolute delta
            trc_diffs.sort(key=lambda x: abs(x["pct_delta"]), reverse=True)

            comparison["volume_chi2"] = {
                "chi2": round(float(chi2), 2),
                "p_value": round(float(p_val), 4),
                "dof": int(dof),
                "significant": p_val < 0.05,
                "trc_diffs": trc_diffs[:10],
            }
        else:
            comparison["volume_chi2"] = {"chi2": 0, "p_value": 1.0, "dof": 0,
                                         "significant": False, "trc_diffs": []}
    else:
        comparison["volume_chi2"] = {"chi2": 0, "p_value": 1.0, "dof": 0,
                                     "significant": False, "trc_diffs": []}

    # ── 2. Mann-Whitney U on sentiment per TRC ──
    sentiment_results = []
    common_trcs = set(stats_a["sentiment_by_trc"].keys()) & set(stats_b["sentiment_by_trc"].keys())
    for trc in sorted(common_trcs):
        vals_a = stats_a["sentiment_by_trc"][trc].get("values", [])
        vals_b = stats_b["sentiment_by_trc"][trc].get("values", [])
        if len(vals_a) >= 5 and len(vals_b) >= 5:
            u_stat, p_val = sp_stats.mannwhitneyu(vals_a, vals_b, alternative='two-sided')
            sentiment_results.append({
                "trc": trc,
                "avg_a": round(np.mean(vals_a), 4),
                "avg_b": round(np.mean(vals_b), 4),
                "delta": round(np.mean(vals_b) - np.mean(vals_a), 4),
                "u_statistic": round(float(u_stat), 1),
                "p_value": round(float(p_val), 4),
                "significant": p_val < 0.05,
                "n_a": len(vals_a),
                "n_b": len(vals_b),
            })
    sentiment_results.sort(key=lambda x: abs(x["delta"]), reverse=True)
    comparison["sentiment_mwu"] = sentiment_results

    # ── 3. Independent t-test on resolution times ──
    res_a = stats_a.get("resolution_times", [])
    res_b = stats_b.get("resolution_times", [])
    if len(res_a) >= 5 and len(res_b) >= 5:
        t_stat, p_val = sp_stats.ttest_ind(res_a, res_b, equal_var=False)
        comparison["resolution_ttest"] = {
            "mean_a": round(float(np.mean(res_a)), 2),
            "mean_b": round(float(np.mean(res_b)), 2),
            "delta": round(float(np.mean(res_b) - np.mean(res_a)), 2),
            "t_statistic": round(float(t_stat), 3),
            "p_value": round(float(p_val), 4),
            "significant": p_val < 0.05,
            "n_a": len(res_a),
            "n_b": len(res_b),
        }
    else:
        comparison["resolution_ttest"] = {
            "mean_a": round(float(np.mean(res_a)), 2) if res_a else None,
            "mean_b": round(float(np.mean(res_b)), 2) if res_b else None,
            "delta": None, "t_statistic": None, "p_value": None,
            "significant": False, "n_a": len(res_a), "n_b": len(res_b),
        }

    # ── 4. TF-IDF rank comparison ──
    terms_a = {_term_name(t): i for i, t in enumerate(stats_a.get("tfidf_top30", []))}
    terms_b = {_term_name(t): i for i, t in enumerate(stats_b.get("tfidf_top30", []))}
    all_terms = set(list(terms_a.keys()) + list(terms_b.keys()))
    rank_diffs = []
    for term in all_terms:
        rank_a = terms_a.get(term)
        rank_b = terms_b.get(term)
        rank_diffs.append({
            "term": term,
            "rank_a": rank_a + 1 if rank_a is not None else None,
            "rank_b": rank_b + 1 if rank_b is not None else None,
            "new_in_b": rank_a is None and rank_b is not None,
            "dropped_in_b": rank_a is not None and rank_b is None,
        })
    rank_diffs.sort(key=lambda x: (
        0 if x["new_in_b"] or x["dropped_in_b"] else 1,
        abs((x["rank_a"] or 99) - (x["rank_b"] or 99))
    ), reverse=False)
    comparison["tfidf_rank_diff"] = rank_diffs[:20]

    return comparison


def build_ab_data_block(stats_a: dict, stats_b: dict, comparison: dict) -> dict:
    """Build a combined data block dict for prompt variable replacement."""
    block = {}

    # Topline
    block["ticket_count"] = stats_a["ticket_count"] + stats_b["ticket_count"]
    block["date_range"] = f"A: {stats_a['date_range']} | B: {stats_b['date_range']}"

    # Volume comparison text
    vol = comparison.get("volume_chi2", {})
    vol_lines = [
        f"Chi-squared: X²={vol.get('chi2', 0)}, p={vol.get('p_value', 1)}, "
        f"{'SIGNIFICANT' if vol.get('significant') else 'not significant'}",
        "",
        "Top TRC Volume Shifts:",
    ]
    for d in vol.get("trc_diffs", [])[:5]:
        vol_lines.append(
            f"  {d['trc']}: {d['count_a']}→{d['count_b']} "
            f"({d['pct_a']:.1f}%→{d['pct_b']:.1f}%, Δ={d['pct_delta']:+.1f}pp)"
        )

    # Sentiment comparison text
    sent = comparison.get("sentiment_mwu", [])
    sent_lines = ["Sentiment Comparison (Mann-Whitney U):"]
    for s in sent[:10]:
        sig = " *" if s["significant"] else ""
        sent_lines.append(
            f"  {s['trc']}: {s['avg_a']:+.3f}→{s['avg_b']:+.3f} "
            f"(Δ={s['delta']:+.3f}, p={s['p_value']:.3f}{sig})"
        )

    # Resolution comparison text
    res = comparison.get("resolution_ttest", {})
    res_line = "Resolution Times: "
    if res.get("p_value") is not None:
        res_line += (
            f"A={res['mean_a']:.1f}h → B={res['mean_b']:.1f}h "
            f"(Δ={res['delta']:+.1f}h, t={res['t_statistic']:.2f}, "
            f"p={res['p_value']:.3f}, "
            f"{'SIGNIFICANT' if res.get('significant') else 'n.s.'})"
        )
    else:
        res_line += "insufficient data for comparison"

    # TF-IDF changes
    rank_lines = ["TF-IDF Rank Changes:"]
    for r in comparison.get("tfidf_rank_diff", [])[:10]:
        if r["new_in_b"]:
            rank_lines.append(f"  {r['term']}: NEW in B (rank #{r['rank_b']})")
        elif r["dropped_in_b"]:
            rank_lines.append(f"  {r['term']}: DROPPED from B (was #{r['rank_a']} in A)")
        elif r["rank_a"] and r["rank_b"]:
            delta = r["rank_a"] - r["rank_b"]
            direction = "↑" if delta > 0 else "↓"
            rank_lines.append(
                f"  {r['term']}: #{r['rank_a']}→#{r['rank_b']} ({direction}{abs(delta)})"
            )

    # Build the full data_block text
    data_block = "\n\n".join([
        f"DATASET A: {stats_a['ticket_count']} tickets, {stats_a['date_range']}",
        f"DATASET B: {stats_b['ticket_count']} tickets, {stats_b['date_range']}",
        "\n".join(vol_lines),
        "\n".join(sent_lines),
        res_line,
        "\n".join(rank_lines),
    ])
    block["data_block"] = data_block

    # Individual field replacements
    block["trc_distribution"] = vol.get("trc_diffs", [])
    block["csat_summary"] = {"dataset_a": stats_a.get("csat_by_trc", {}),
                              "dataset_b": stats_b.get("csat_by_trc", {})}
    block["sentiment_by_trc"] = {}
    block["incident_flags"] = []
    block["top_terms"] = stats_b.get("tfidf_top30", [])
    block["rising_terms"] = []
    block["correlations"] = []
    block["redacted_samples"] = []
    block["intervention_context"] = []
    block["payer_distribution"] = []
    block["product_area_distribution"] = []
    block["product_gap_flags"] = []
    block["resolution_times"] = {}

    return block


def _term_name(t):
    """Extract term name from various formats."""
    if isinstance(t, dict):
        return t.get("term", t.get("word", ""))
    elif isinstance(t, (list, tuple)) and len(t) >= 1:
        return t[0]
    return str(t)
