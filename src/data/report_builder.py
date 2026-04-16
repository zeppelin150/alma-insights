"""
Alma Insights — Report Builder (Pass 3.0)
Pre-computes all analytics into a data block for prompt variable replacement.
Gemini gets RESULTS, not raw data. No file references cross the network.
"""

import re
import json
from datetime import datetime


def build_data_block(db, date_start, date_end, trc_filter=None, dataset_id=None,
                     source_id=None):
    """Pre-compute ALL analytics. Gemini gets structured results, not raw data.

    Returns dict with keys that map to prompt {variables}.
    """
    from src.data.source_registry import SourceRegistry
    from src.data.warehouse_query import WarehouseQuery
    registry = SourceRegistry(db.conn)
    wq = WarehouseQuery(db.conn, registry)

    block = {}

    # ── Topline stats ──
    conditions = ["created_at >= ?", "created_at <= ?"]
    params = [date_start, date_end]
    if trc_filter:
        conditions.append("trc_code = ?")
        params.append(trc_filter)
    if dataset_id is not None:
        conditions.append("dataset_id = ?")
        params.append(dataset_id)
    where = " AND ".join(conditions)

    cnt_rows = wq.query_conversations_raw(
        f"SELECT COUNT(*) as cnt FROM {{table}} WHERE {where}", params,
        source_id=source_id,
    )
    block["ticket_count"] = sum(r[0] for r in cnt_rows if r and r[0])
    block["date_range"] = f"{date_start} to {date_end}"

    # TRC distribution
    trc_rows = wq.query_conversations_raw(f"""
        SELECT trc_code, COUNT(*) as cnt,
               AVG(csat_score) as avg_csat,
               AVG(message_count) as avg_msgs
        FROM {{table}}
        WHERE {where} AND trc_code != ''
        GROUP BY trc_code ORDER BY cnt DESC
    """, params, source_id=source_id)
    # Aggregate TRC counts across sources
    _trc_agg = {}
    for r in trc_rows:
        trc = r[0]
        if trc not in _trc_agg:
            _trc_agg[trc] = {"count": 0, "csat_sum": 0.0, "csat_n": 0, "msgs_sum": 0.0, "msgs_n": 0}
        _trc_agg[trc]["count"] += r[1]
        if r[2] is not None:
            _trc_agg[trc]["csat_sum"] += r[2] * r[1]
            _trc_agg[trc]["csat_n"] += r[1]
        if r[3] is not None:
            _trc_agg[trc]["msgs_sum"] += r[3] * r[1]
            _trc_agg[trc]["msgs_n"] += r[1]
    block["trc_distribution"] = sorted([
        {"trc": t, "count": v["count"],
         "avg_csat": round(v["csat_sum"] / v["csat_n"], 2) if v["csat_n"] else None,
         "avg_messages": round(v["msgs_sum"] / v["msgs_n"], 1) if v["msgs_n"] else None}
        for t, v in _trc_agg.items()
    ], key=lambda x: x["count"], reverse=True)

    # CSAT summary
    csat_rows = wq.query_conversations_raw(f"""
        SELECT AVG(csat_score) as avg, MIN(csat_score) as min_c,
               MAX(csat_score) as max_c, COUNT(csat_score) as rated
        FROM {{table}}
        WHERE {where} AND csat_score IS NOT NULL
    """, params, source_id=source_id)
    _csat_total = 0.0
    _csat_count = 0
    _csat_min = None
    _csat_max = None
    _csat_rated = 0
    for r in csat_rows:
        if r[0] is not None and r[3]:
            _csat_total += r[0] * r[3]
            _csat_count += r[3]
            _csat_min = r[1] if _csat_min is None else min(_csat_min, r[1])
            _csat_max = r[2] if _csat_max is None else max(_csat_max, r[2])
            _csat_rated += r[3]
    block["csat_summary"] = {
        "average": round(_csat_total / _csat_count, 2) if _csat_count else None,
        "min": _csat_min, "max": _csat_max,
        "rated_count": _csat_rated,
    }

    # Resolution times — JOIN tickets and conversations per source
    _c_where = where.replace('created_at', 'c.created_at').replace('trc_code', 'c.trc_code').replace('dataset_id', 'c.dataset_id')
    # For resolution times, we need to join per-source tickets with conversations
    # Use the tickets table routing since tickets have the resolution columns
    _res_rows = []
    for src in registry.list_sources():
        prefix = src["table_prefix"]
        try:
            r = db.conn.execute(f"""
                SELECT AVG(t.assignment_to_resolution_hours) as avg_assign_res,
                       AVG(t.total_resolution_hours) as avg_total_res,
                       AVG(t.first_reply_hours) as avg_first_reply
                FROM [{prefix}_tickets] t
                JOIN [{prefix}_conversations] c ON t.ticket_id = c.ticket_id
                WHERE {_c_where}
            """, params).fetchone()
            if r:
                _res_rows.append(r)
        except Exception:
            pass
    # Aggregate resolution times
    _assign_vals = [r[0] for r in _res_rows if r[0] is not None]
    _total_vals = [r[1] for r in _res_rows if r[1] is not None]
    _reply_vals = [r[2] for r in _res_rows if r[2] is not None]
    block["resolution_times"] = {
        "avg_assignment_to_resolution": round(sum(_assign_vals) / len(_assign_vals), 1) if _assign_vals else None,
        "avg_total_resolution": round(sum(_total_vals) / len(_total_vals), 1) if _total_vals else None,
        "avg_first_reply": round(sum(_reply_vals) / len(_reply_vals), 1) if _reply_vals else None,
    }

    # ── TF-IDF & Sentiment (via trending_engine) ──
    try:
        from src.data.trending_engine import run_full_analysis
        from src.data.connection_factory import get_connection
        conn = get_connection(db.db_path)
        analysis = run_full_analysis(
            conn, date_start, date_end,
            trc_filter=trc_filter, window_size="Weekly",
            topic_method="nmf", db=db,
        )
        conn.close()

        # Top terms
        terms_data = analysis.get("terms", {})
        all_terms = terms_data.get("all_terms", [])
        block["top_terms"] = all_terms[:50] if all_terms else []
        block["rising_terms"] = terms_data.get("rising_terms", [])[:20]

        # Sentiment by TRC
        sentiment = analysis.get("sentiment", {})
        block["sentiment_by_trc"] = sentiment

        # Correlations
        block["correlations"] = analysis.get("correlations", [])[:10]

        # Topics
        block["topics"] = analysis.get("topics", [])
    except Exception:
        block["top_terms"] = []
        block["rising_terms"] = []
        block["sentiment_by_trc"] = {}
        block["correlations"] = []
        block["topics"] = []

    # ── Incident flags ──
    try:
        from src.data.incident_engine import run_incident_scan
        scan = run_incident_scan(db, target_date=date_end)
        flags = scan.get("new_flags", [])
        block["incident_flags"] = [
            {"trc": f.get("trc_code", ""), "type": f.get("flag_type", ""),
             "observed": f.get("observed_value", 0),
             "expected": f.get("expected_lambda", 0),
             "p_value": f.get("p_value", None)}
            for f in flags
        ]
        block["trc_results"] = scan.get("trc_results", [])
    except Exception:
        block["incident_flags"] = []
        block["trc_results"] = []

    # ── Interventions ──
    try:
        interventions = db.get_interventions(date_from=date_start, date_to=date_end)
        block["intervention_context"] = interventions
    except Exception:
        block["intervention_context"] = []

    # ── Entity distributions ──
    try:
        block["payer_distribution"] = db.get_entity_distribution(
            "payer", date_from=date_start, date_to=date_end
        )
        block["product_area_distribution"] = db.get_entity_distribution(
            "product_area", date_from=date_start, date_to=date_end
        )
    except Exception:
        block["payer_distribution"] = []
        block["product_area_distribution"] = []

    # ── Redacted samples ──
    try:
        from src.gemini.gemini_client import GeminiClient
        samples = wq.query_conversations_raw(f"""
            SELECT thread_preview FROM {{table}}
            WHERE {where} AND thread_preview != ''
            ORDER BY RANDOM() LIMIT 10
        """, params, source_id=source_id)
        redacted = []
        for s in samples:
            text = GeminiClient._redact_base(None, s[0] if not hasattr(s, 'keys') else s["thread_preview"])
            redacted.append(text[:500])
        block["redacted_samples"] = redacted
    except Exception:
        block["redacted_samples"] = []

    # ── Product gap flags (if engine available) ──
    try:
        from src.data.product_gap_engine import detect_product_gaps
        block["product_gap_flags"] = detect_product_gaps(db, date_start, date_end)
    except Exception:
        block["product_gap_flags"] = []

    # ── Structured JSON output (Session 3) ──
    block["structured_json"] = build_structured_output(block)

    return block


def build_structured_output(block: dict) -> str:
    """Build a structured JSON summary from the data block.

    Returns a JSON string with standardized sections that
    query_report can serve by section name.
    """
    import json

    structured = {
        "findings": _extract_findings(block),
        "summary_stats": _extract_summary_stats(block),
        "recommendations": _extract_recommendations(block),
    }
    return json.dumps(structured, default=str)


def _extract_findings(block: dict) -> list[dict]:
    """Extract findings from TRC distribution and incident flags."""
    findings = []

    for trc_item in block.get("trc_distribution", []):
        findings.append({
            "type": "trc_concentration",
            "title": f"{trc_item.get('trc', 'Unknown')} tickets",
            "count": trc_item.get("count", 0),
            "percentage": trc_item.get("pct", 0),
        })

    for flag in block.get("incident_flags", []):
        findings.append({
            "type": "incident",
            "title": f"{flag.get('trc', '')} — {flag.get('type', '')}",
            "severity": flag.get("severity", "unknown"),
            "observed": flag.get("observed", 0),
        })

    return findings


def _extract_summary_stats(block: dict) -> dict:
    """Extract key summary statistics."""
    return {
        "ticket_count": block.get("ticket_count", 0),
        "date_range": block.get("date_range", ""),
        "csat_summary": block.get("csat_summary", {}),
        "resolution_times": block.get("resolution_times", {}),
        "trc_count": len(block.get("trc_distribution", [])),
        "incident_count": len(block.get("incident_flags", [])),
    }


def _extract_recommendations(block: dict) -> list[str]:
    """Generate recommendations from data patterns."""
    recs = []
    csat = block.get("csat_summary", {})
    if csat.get("average") and csat["average"] < 3.0:
        recs.append("CSAT average is below 3.0 — investigate top complaint categories")

    incidents = block.get("incident_flags", [])
    critical = [f for f in incidents if f.get("severity") == "critical"]
    if critical:
        recs.append(f"{len(critical)} critical incidents detected — prioritize investigation")

    gaps = block.get("product_gap_flags", [])
    if gaps:
        recs.append(f"{len(gaps)} product gap signals identified — review with product team")

    if not recs:
        recs.append("No critical issues detected — continue monitoring")

    return recs


def format_data_block_for_prompt(block):
    """Convert the data block dict into structured text for prompt injection."""
    sections = [
        _fmt_topline(block),
        _fmt_trc_distribution(block),
        _fmt_csat_summary(block),
        _fmt_resolution_times(block),
        _fmt_top_terms(block),
        _fmt_rising_terms(block),
        _fmt_sentiment_by_trc(block),
        _fmt_correlations(block),
        _fmt_incident_flags(block),
        _fmt_interventions(block),
        _fmt_entity_distributions(block),
        _fmt_product_gap_flags(block),
        _fmt_redacted_samples(block),
    ]
    return "\n\n".join(s for s in sections if s)


def _fmt_topline(block):
    return (f"TOPLINE: {block.get('ticket_count', 0)} tickets, "
            f"Date range: {block.get('date_range', 'N/A')}")


def _fmt_trc_distribution(block):
    trc_dist = block.get("trc_distribution", [])
    if not trc_dist:
        return ""
    lines = ["TRC DISTRIBUTION:"]
    for t in trc_dist[:15]:
        csat = f", CSAT={t['avg_csat']}" if t.get('avg_csat') else ""
        lines.append(f"  {t['trc']}: {t['count']} tickets{csat}")
    return "\n".join(lines)


def _fmt_csat_summary(block):
    csat = block.get("csat_summary", {})
    if not csat.get("average"):
        return ""
    return (f"CSAT SUMMARY: avg={csat['average']}, "
            f"range={csat.get('min', 'N/A')}-{csat.get('max', 'N/A')}, "
            f"rated={csat.get('rated_count', 0)} tickets")


def _fmt_resolution_times(block):
    res = block.get("resolution_times", {})
    if not any(v for v in res.values() if v is not None):
        return ""
    return (f"RESOLUTION TIMES: "
            f"assignment-to-resolution={res.get('avg_assignment_to_resolution', 'N/A')}h, "
            f"total={res.get('avg_total_resolution', 'N/A')}h, "
            f"first-reply={res.get('avg_first_reply', 'N/A')}h")


def _fmt_top_terms(block):
    top_terms = block.get("top_terms", [])
    if not top_terms:
        return ""
    lines = ["TOP TERMS (TF-IDF):"]
    for t in top_terms[:30]:
        if isinstance(t, dict):
            lines.append(f"  {t.get('term', t.get('word', ''))}: "
                       f"score={t.get('score', t.get('tfidf', 'N/A'))}")
        elif isinstance(t, (list, tuple)) and len(t) >= 2:
            lines.append(f"  {t[0]}: score={t[1]:.4f}")
        else:
            lines.append(f"  {t}")
    return "\n".join(lines)


def _fmt_rising_terms(block):
    rising = block.get("rising_terms", [])
    if not rising:
        return ""
    lines = ["RISING TERMS (velocity):"]
    for t in rising[:15]:
        if isinstance(t, dict):
            lines.append(f"  {t.get('term', '')}: velocity={t.get('velocity', 'N/A')}")
        elif isinstance(t, (list, tuple)) and len(t) >= 2:
            lines.append(f"  {t[0]}: velocity={t[1]:.4f}")
        else:
            lines.append(f"  {t}")
    return "\n".join(lines)


def _fmt_sentiment_by_trc(block):
    sentiment = block.get("sentiment_by_trc", {})
    if not sentiment:
        return ""
    lines = ["SENTIMENT BY TRC:"]
    for trc, data in sentiment.items():
        if isinstance(data, list) and data:
            vals = [d[1] if isinstance(d, (list, tuple)) else d.get("compound", 0)
                    for d in data if (isinstance(d, (list, tuple)) and len(d) >= 2)
                    or isinstance(d, dict)]
            avg = sum(vals) / len(vals) if vals else 0
            lines.append(f"  {trc}: avg_compound={avg:.3f} ({len(data)} windows)")
        elif isinstance(data, (int, float)):
            lines.append(f"  {trc}: compound={data:.3f}")
    return "\n".join(lines)


def _fmt_correlations(block):
    corrs = block.get("correlations", [])
    if not corrs:
        return ""
    lines = ["CROSS-TRC CORRELATIONS:"]
    for c in corrs[:10]:
        if isinstance(c, dict):
            lines.append(f"  {c.get('trc_a', '')} <-> {c.get('trc_b', '')}: "
                       f"r={c.get('correlation', 'N/A')}")
        elif isinstance(c, (list, tuple)) and len(c) >= 3:
            lines.append(f"  {c[0]} <-> {c[1]}: r={c[2]:.3f}")
    return "\n".join(lines)


def _fmt_incident_flags(block):
    flags = block.get("incident_flags", [])
    if not flags:
        return ""
    lines = ["INCIDENT FLAGS:"]
    for f in flags:
        pval = f"p={f['p_value']:.4f}" if f.get("p_value") else ""
        lines.append(f"  {f['trc']}: {f['type']}, "
                   f"observed={f['observed']}, expected={f['expected']} {pval}")
    return "\n".join(lines)


def _fmt_interventions(block):
    interventions = block.get("intervention_context", [])
    if not interventions:
        return ""
    lines = ["RECENT INTERVENTIONS:"]
    for i in interventions:
        trcs = i.get("affected_trcs", "")
        if isinstance(trcs, str):
            try:
                trcs = json.loads(trcs)
            except (json.JSONDecodeError, TypeError):
                trcs = []
        trc_str = ", ".join(trcs) if trcs else "all TRCs"
        lines.append(f"  {i['event_date']}: {i['name']} ({i['category']}) "
                   f"-- affects {trc_str}")
    return "\n".join(lines)


def _fmt_entity_distributions(block):
    parts = []
    payers = block.get("payer_distribution", [])
    if payers:
        lines = ["PAYER DISTRIBUTION:"]
        for p in payers[:10]:
            lines.append(f"  {p['entity_value']}: {p['ticket_count']} tickets")
        parts.append("\n".join(lines))

    products = block.get("product_area_distribution", [])
    if products:
        lines = ["PRODUCT AREA DISTRIBUTION:"]
        for p in products[:10]:
            lines.append(f"  {p['entity_value']}: {p['ticket_count']} tickets")
        parts.append("\n".join(lines))

    return "\n\n".join(parts)


def _fmt_product_gap_flags(block):
    gaps = block.get("product_gap_flags", [])
    if not gaps:
        return ""
    lines = ["PRODUCT GAP FLAGS:"]
    for g in gaps:
        if g.get("is_flagged"):
            lines.append(
                f"  {g['product_area']}: {g['trc_count']} TRCs, "
                f"volume velocity={g.get('volume_velocity', 0):+.0%}, "
                f"sentiment delta={g.get('sentiment_delta', 0):+.3f}, "
                f"gap_score={g.get('gap_score', 0):.1f} FLAGGED"
            )
    return "\n".join(lines) if len(lines) > 1 else ""


def _fmt_redacted_samples(block):
    samples = block.get("redacted_samples", [])
    if not samples:
        return ""
    lines = ["SAMPLE CONVERSATIONS (redacted):"]
    for i, s in enumerate(samples, 1):
        lines.append(f"  [{i}] {s[:300]}")
    return "\n".join(lines)


def replace_prompt_variables(prompt_text, block, db=None):
    """Replace {variable} tokens in prompt text with formatted data block values.

    Build 7.0: If db is provided and prompt contains {temporal_context},
    builds windowed data blocks for chronological narrative.
    """
    formatted = format_data_block_for_prompt(block)

    # Build 7.0: Temporal context (only if token present and db available)
    temporal_ctx = "(No temporal data available)"
    if "{temporal_context}" in prompt_text and db:
        try:
            date_range = block.get("date_range", "")
            if " to " in date_range:
                d_start, d_end = date_range.split(" to ")
                windowed = build_windowed_data_blocks(db, d_start, d_end)
                temporal_ctx = format_temporal_context(windowed)
        except Exception:
            pass

    replacements = {
        "{ticket_count}": str(block.get("ticket_count", 0)),
        "{date_range}": block.get("date_range", "N/A"),
        "{data_block}": formatted,
        "{trc_distribution}": _format_section(block.get("trc_distribution", []),
                                               lambda t: f"{t['trc']}: {t['count']}"),
        "{csat_summary}": _format_csat(block.get("csat_summary", {})),
        "{sentiment_by_trc}": _format_sentiment(block.get("sentiment_by_trc", {})),
        "{incident_flags}": _format_flags(block.get("incident_flags", [])),
        "{top_terms}": _format_terms(block.get("top_terms", [])),
        "{rising_terms}": _format_terms(block.get("rising_terms", [])),
        "{correlations}": _format_correlations(block.get("correlations", [])),
        "{redacted_samples}": "\n".join(
            f"[{i+1}] {s[:300]}" for i, s in enumerate(block.get("redacted_samples", []))
        ),
        "{intervention_context}": _format_interventions(block.get("intervention_context", [])),
        "{payer_distribution}": _format_entity_dist(block.get("payer_distribution", [])),
        "{product_area_distribution}": _format_entity_dist(block.get("product_area_distribution", [])),
        "{product_gap_flags}": _format_gaps(block.get("product_gap_flags", [])),
        "{temporal_context}": temporal_ctx,
    }

    result = prompt_text
    for token, value in replacements.items():
        result = result.replace(token, value)
    return result


def _validate_prompt_before_send(prompt):
    """Validate prompt has no unreplaced tokens or file references."""
    unreplaced = re.findall(r'\{[a-z_]+\}', prompt)
    if unreplaced:
        raise ValueError(f"Unreplaced tokens in prompt: {unreplaced}")
    if any(p in prompt.lower() for p in ['file://', 'http://', '.jsonl', '.csv']):
        raise ValueError("Prompt contains file/URL references — data must be inlined")


# ── Formatting helpers ──

def _format_section(items, fmt_fn):
    if not items:
        return "No data available"
    return "\n".join(fmt_fn(item) for item in items[:15])


def _format_csat(csat):
    if not csat or not csat.get("average"):
        return "No CSAT data"
    return (f"avg={csat['average']}, range={csat.get('min', 'N/A')}-{csat.get('max', 'N/A')}, "
            f"rated={csat.get('rated_count', 0)}")


def _format_sentiment(sentiment):
    if not sentiment:
        return "No sentiment data"
    lines = []
    for trc, data in sentiment.items():
        if isinstance(data, list) and data:
            vals = [d[1] if isinstance(d, (list, tuple)) and len(d) >= 2
                    else d.get("compound", 0) if isinstance(d, dict) else 0
                    for d in data]
            avg = sum(vals) / len(vals) if vals else 0
            lines.append(f"{trc}: avg_compound={avg:.3f}")
        elif isinstance(data, (int, float)):
            lines.append(f"{trc}: compound={data:.3f}")
    return "\n".join(lines) if lines else "No sentiment data"


def _format_flags(flags):
    if not flags:
        return "No active incident flags"
    lines = []
    for f in flags:
        pval = f", p={f['p_value']:.4f}" if f.get("p_value") else ""
        lines.append(f"{f['trc']}: {f['type']}, observed={f['observed']}, "
                    f"expected={f['expected']}{pval}")
    return "\n".join(lines)


def _format_terms(terms):
    if not terms:
        return "No term data"
    lines = []
    for t in terms[:20]:
        if isinstance(t, dict):
            lines.append(f"{t.get('term', t.get('word', ''))}: "
                       f"{t.get('score', t.get('tfidf', t.get('velocity', 'N/A')))}")
        elif isinstance(t, (list, tuple)) and len(t) >= 2:
            lines.append(f"{t[0]}: {t[1]:.4f}")
        else:
            lines.append(str(t))
    return "\n".join(lines)


def _format_correlations(corrs):
    if not corrs:
        return "No correlation data"
    lines = []
    for c in corrs[:10]:
        if isinstance(c, dict):
            lines.append(f"{c.get('trc_a', '')} <-> {c.get('trc_b', '')}: "
                       f"r={c.get('correlation', 'N/A')}")
        elif isinstance(c, (list, tuple)) and len(c) >= 3:
            lines.append(f"{c[0]} <-> {c[1]}: r={c[2]:.3f}")
    return "\n".join(lines)


def _format_interventions(interventions):
    if not interventions:
        return "No recent interventions"
    lines = []
    for i in interventions:
        trcs = i.get("affected_trcs", "")
        if isinstance(trcs, str):
            try:
                trcs = json.loads(trcs)
            except (json.JSONDecodeError, TypeError):
                trcs = []
        trc_str = ", ".join(trcs) if trcs else "all TRCs"
        lines.append(f"{i['event_date']}: {i['name']} ({i['category']}) -- affects {trc_str}")
    return "\n".join(lines)


def _format_entity_dist(dist):
    if not dist:
        return "No entity data"
    return "\n".join(f"{d['entity_value']}: {d['ticket_count']} tickets" for d in dist[:10])


def _format_gaps(gaps):
    if not gaps:
        return "No product gap data"
    lines = []
    for g in gaps:
        flagged = "FLAGGED" if g.get("is_flagged") else "not flagged"
        lines.append(
            f"{g['product_area']}: {g['trc_count']} TRCs, "
            f"volume={g.get('volume_velocity', 0):+.0%}, "
            f"sentiment_delta={g.get('sentiment_delta', 0):+.3f}, "
            f"score={g.get('gap_score', 0):.1f} {flagged}"
        )
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
#  WINDOWED TEMPORAL CONTEXT (Build 7.0)
# ═══════════════════════════════════════════════════════════════

def _generate_windows(date_start, date_end, window="weekly"):
    """Generate (start, end, label) tuples for the given date range.

    Supports 'weekly' and 'biweekly' window sizes.
    """
    from datetime import datetime, timedelta

    d_start = datetime.strptime(date_start, "%Y-%m-%d")
    d_end = datetime.strptime(date_end, "%Y-%m-%d")

    step_days = 7 if window == "weekly" else 14
    windows = []
    idx = 1
    cursor = d_start

    while cursor < d_end:
        w_end = min(cursor + timedelta(days=step_days - 1), d_end)
        label = f"Week {idx}" if window == "weekly" else f"Period {idx}"
        label += f" ({cursor.strftime('%b %d')}-{w_end.strftime('%b %d')})"
        windows.append((
            cursor.strftime("%Y-%m-%d"),
            w_end.strftime("%Y-%m-%d"),
            label,
        ))
        cursor = w_end + timedelta(days=1)
        idx += 1

    return windows


def build_windowed_data_blocks(db, date_start, date_end,
                                trc_filter=None, window="weekly"):
    """Build lightweight per-window stats for temporal narrative (Build 7.0).

    Returns list of dicts with per-window metrics.  Each dict contains:
      window, ticket_count, top_3_terms, sentiment_avg,
      rising_terms, incident_flag_count, delta_pct (volume change vs. prior window)
    """
    windows = _generate_windows(date_start, date_end, window)
    blocks = []
    prev_count = None

    for w_start, w_end, label in windows:
        try:
            mini = build_data_block(db, w_start, w_end, trc_filter)
        except Exception:
            blocks.append({
                "window": label,
                "ticket_count": 0,
                "top_3_terms": [],
                "sentiment_avg": None,
                "rising_terms": [],
                "incident_flag_count": 0,
                "delta_pct": None,
            })
            continue

        ticket_count = mini.get("ticket_count", 0)

        # Extract top 3 terms
        top_terms_raw = mini.get("top_terms", [])
        top_3 = []
        for t in top_terms_raw[:3]:
            if isinstance(t, dict):
                top_3.append(t.get("term", t.get("word", "")))
            elif isinstance(t, (list, tuple)) and len(t) >= 1:
                top_3.append(str(t[0]))

        # Sentiment average
        csat = mini.get("csat_summary", {})
        sentiment_avg = csat.get("average")

        # Rising terms (top 3)
        rising_raw = mini.get("rising_terms", [])
        rising = []
        for t in rising_raw[:3]:
            if isinstance(t, dict):
                rising.append(t.get("term", ""))
            elif isinstance(t, (list, tuple)) and len(t) >= 1:
                rising.append(str(t[0]))

        # Incident flags count
        flag_count = len(mini.get("incident_flags", []))

        # Volume delta vs prior window
        delta_pct = None
        if prev_count is not None and prev_count > 0:
            delta_pct = round((ticket_count - prev_count) / prev_count * 100, 1)
        prev_count = ticket_count

        blocks.append({
            "window": label,
            "ticket_count": ticket_count,
            "top_3_terms": top_3,
            "sentiment_avg": sentiment_avg,
            "rising_terms": rising,
            "incident_flag_count": flag_count,
            "delta_pct": delta_pct,
        })

    return blocks


def format_temporal_context(windowed_blocks):
    """Format windowed blocks as a chronological narrative for prompt injection.

    Returns structured text showing week-over-week progression with
    volume deltas, sentiment shifts, emerging terms, and incident flags.
    """
    if not windowed_blocks:
        return "(No temporal data available)"

    lines = ["TEMPORAL PROGRESSION (week-over-week):"]

    for wb in windowed_blocks:
        parts = [f"  {wb['window']}: {wb['ticket_count']} tickets"]

        # Volume delta
        if wb.get("delta_pct") is not None:
            sign = "+" if wb["delta_pct"] >= 0 else ""
            parts[0] += f" ({sign}{wb['delta_pct']}%)"

        # Sentiment
        if wb.get("sentiment_avg") is not None:
            parts.append(f"    CSAT avg: {wb['sentiment_avg']:.2f}")

        # Top terms
        if wb.get("top_3_terms"):
            terms_str = ", ".join(f'"{t}"' for t in wb["top_3_terms"])
            parts.append(f"    Top terms: {terms_str}")

        # Rising terms
        if wb.get("rising_terms"):
            rising_str = ", ".join(f'"{t}"' for t in wb["rising_terms"])
            parts.append(f"    Rising: {rising_str}")

        # Incident flags
        if wb.get("incident_flag_count", 0) > 0:
            parts.append(f"    Incident flags: {wb['incident_flag_count']}")

        lines.append("\n".join(parts))

    return "\n".join(lines)
