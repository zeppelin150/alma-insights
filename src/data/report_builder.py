"""
Alma Insights — Report Builder (Pass 3.0)
Pre-computes all analytics into a data block for prompt variable replacement.
Gemini gets RESULTS, not raw data. No file references cross the network.
"""

import re
import json
from datetime import datetime


def build_data_block(db, date_start, date_end, trc_filter=None, dataset_id=None):
    """Pre-compute ALL analytics. Gemini gets structured results, not raw data.

    Returns dict with keys that map to prompt {variables}.
    """
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

    row = db.conn.execute(
        f"SELECT COUNT(*) as cnt FROM conversations WHERE {where}", params
    ).fetchone()
    block["ticket_count"] = row["cnt"]
    block["date_range"] = f"{date_start} to {date_end}"

    # TRC distribution
    trc_rows = db.conn.execute(f"""
        SELECT trc_code, COUNT(*) as cnt,
               AVG(csat_score) as avg_csat,
               AVG(message_count) as avg_msgs
        FROM conversations
        WHERE {where} AND trc_code != ''
        GROUP BY trc_code ORDER BY cnt DESC
    """, params).fetchall()
    block["trc_distribution"] = [
        {"trc": r["trc_code"], "count": r["cnt"],
         "avg_csat": round(r["avg_csat"], 2) if r["avg_csat"] else None,
         "avg_messages": round(r["avg_msgs"], 1) if r["avg_msgs"] else None}
        for r in trc_rows
    ]

    # CSAT summary
    csat_row = db.conn.execute(f"""
        SELECT AVG(csat_score) as avg, MIN(csat_score) as min_c,
               MAX(csat_score) as max_c, COUNT(csat_score) as rated
        FROM conversations
        WHERE {where} AND csat_score IS NOT NULL
    """, params).fetchone()
    block["csat_summary"] = {
        "average": round(csat_row["avg"], 2) if csat_row["avg"] else None,
        "min": csat_row["min_c"], "max": csat_row["max_c"],
        "rated_count": csat_row["rated"],
    }

    # Resolution times
    res_row = db.conn.execute(f"""
        SELECT AVG(t.assignment_to_resolution_hours) as avg_assign_res,
               AVG(t.total_resolution_hours) as avg_total_res,
               AVG(t.first_reply_hours) as avg_first_reply
        FROM tickets t
        JOIN conversations c ON t.ticket_id = c.ticket_id
        WHERE {where.replace('created_at', 'c.created_at')}
    """, params).fetchone()
    block["resolution_times"] = {
        "avg_assignment_to_resolution": round(res_row["avg_assign_res"], 1) if res_row["avg_assign_res"] else None,
        "avg_total_resolution": round(res_row["avg_total_res"], 1) if res_row["avg_total_res"] else None,
        "avg_first_reply": round(res_row["avg_first_reply"], 1) if res_row["avg_first_reply"] else None,
    }

    # ── TF-IDF & Sentiment (via trending_engine) ──
    try:
        from src.data.trending_engine import run_full_analysis
        import sqlite3
        conn = sqlite3.connect(str(db.db_path))
        conn.row_factory = sqlite3.Row
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
        samples = db.conn.execute(f"""
            SELECT thread_preview FROM conversations
            WHERE {where} AND thread_preview != ''
            ORDER BY RANDOM() LIMIT 10
        """, params).fetchall()
        redacted = []
        for s in samples:
            text = GeminiClient._redact_base(None, s["thread_preview"])
            redacted.append(text[:500])  # Truncate long previews
        block["redacted_samples"] = redacted
    except Exception:
        block["redacted_samples"] = []

    # ── Product gap flags (if engine available) ──
    try:
        from src.data.product_gap_engine import detect_product_gaps
        block["product_gap_flags"] = detect_product_gaps(db, date_start, date_end)
    except Exception:
        block["product_gap_flags"] = []

    return block


def format_data_block_for_prompt(block):
    """Convert the data block dict into structured text for prompt injection."""
    sections = []

    # Topline
    sections.append(f"TOPLINE: {block.get('ticket_count', 0)} tickets, "
                    f"Date range: {block.get('date_range', 'N/A')}")

    # TRC Distribution
    trc_dist = block.get("trc_distribution", [])
    if trc_dist:
        lines = ["TRC DISTRIBUTION:"]
        for t in trc_dist[:15]:
            csat = f", CSAT={t['avg_csat']}" if t.get('avg_csat') else ""
            lines.append(f"  {t['trc']}: {t['count']} tickets{csat}")
        sections.append("\n".join(lines))

    # CSAT Summary
    csat = block.get("csat_summary", {})
    if csat.get("average"):
        sections.append(
            f"CSAT SUMMARY: avg={csat['average']}, "
            f"range={csat.get('min', 'N/A')}-{csat.get('max', 'N/A')}, "
            f"rated={csat.get('rated_count', 0)} tickets"
        )

    # Resolution Times
    res = block.get("resolution_times", {})
    if any(v for v in res.values() if v is not None):
        sections.append(
            f"RESOLUTION TIMES: "
            f"assignment-to-resolution={res.get('avg_assignment_to_resolution', 'N/A')}h, "
            f"total={res.get('avg_total_resolution', 'N/A')}h, "
            f"first-reply={res.get('avg_first_reply', 'N/A')}h"
        )

    # Top Terms
    top_terms = block.get("top_terms", [])
    if top_terms:
        lines = ["TOP TERMS (TF-IDF):"]
        for t in top_terms[:30]:
            if isinstance(t, dict):
                lines.append(f"  {t.get('term', t.get('word', ''))}: "
                           f"score={t.get('score', t.get('tfidf', 'N/A'))}")
            elif isinstance(t, (list, tuple)) and len(t) >= 2:
                lines.append(f"  {t[0]}: score={t[1]:.4f}")
            else:
                lines.append(f"  {t}")
        sections.append("\n".join(lines))

    # Rising Terms
    rising = block.get("rising_terms", [])
    if rising:
        lines = ["RISING TERMS (velocity):"]
        for t in rising[:15]:
            if isinstance(t, dict):
                lines.append(f"  {t.get('term', '')}: velocity={t.get('velocity', 'N/A')}")
            elif isinstance(t, (list, tuple)) and len(t) >= 2:
                lines.append(f"  {t[0]}: velocity={t[1]:.4f}")
            else:
                lines.append(f"  {t}")
        sections.append("\n".join(lines))

    # Sentiment by TRC
    sentiment = block.get("sentiment_by_trc", {})
    if sentiment:
        lines = ["SENTIMENT BY TRC:"]
        for trc, data in sentiment.items():
            if isinstance(data, list) and data:
                # Average compound across windows
                vals = [d[1] if isinstance(d, (list, tuple)) else d.get("compound", 0)
                        for d in data if (isinstance(d, (list, tuple)) and len(d) >= 2)
                        or isinstance(d, dict)]
                avg = sum(vals) / len(vals) if vals else 0
                lines.append(f"  {trc}: avg_compound={avg:.3f} ({len(data)} windows)")
            elif isinstance(data, (int, float)):
                lines.append(f"  {trc}: compound={data:.3f}")
        sections.append("\n".join(lines))

    # Correlations
    corrs = block.get("correlations", [])
    if corrs:
        lines = ["CROSS-TRC CORRELATIONS:"]
        for c in corrs[:10]:
            if isinstance(c, dict):
                lines.append(f"  {c.get('trc_a', '')} <-> {c.get('trc_b', '')}: "
                           f"r={c.get('correlation', 'N/A')}")
            elif isinstance(c, (list, tuple)) and len(c) >= 3:
                lines.append(f"  {c[0]} <-> {c[1]}: r={c[2]:.3f}")
        sections.append("\n".join(lines))

    # Incident Flags
    flags = block.get("incident_flags", [])
    if flags:
        lines = ["INCIDENT FLAGS:"]
        for f in flags:
            pval = f"p={f['p_value']:.4f}" if f.get("p_value") else ""
            lines.append(f"  {f['trc']}: {f['type']}, "
                       f"observed={f['observed']}, expected={f['expected']} {pval}")
        sections.append("\n".join(lines))

    # Interventions
    interventions = block.get("intervention_context", [])
    if interventions:
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
        sections.append("\n".join(lines))

    # Entity distributions
    payers = block.get("payer_distribution", [])
    if payers:
        lines = ["PAYER DISTRIBUTION:"]
        for p in payers[:10]:
            lines.append(f"  {p['entity_value']}: {p['ticket_count']} tickets")
        sections.append("\n".join(lines))

    products = block.get("product_area_distribution", [])
    if products:
        lines = ["PRODUCT AREA DISTRIBUTION:"]
        for p in products[:10]:
            lines.append(f"  {p['entity_value']}: {p['ticket_count']} tickets")
        sections.append("\n".join(lines))

    # Product gap flags
    gaps = block.get("product_gap_flags", [])
    if gaps:
        lines = ["PRODUCT GAP FLAGS:"]
        for g in gaps:
            if g.get("is_flagged"):
                lines.append(
                    f"  {g['product_area']}: {g['trc_count']} TRCs, "
                    f"volume velocity={g.get('volume_velocity', 0):+.0%}, "
                    f"sentiment delta={g.get('sentiment_delta', 0):+.3f}, "
                    f"gap_score={g.get('gap_score', 0):.1f} FLAGGED"
                )
        sections.append("\n".join(lines))

    # Redacted samples
    samples = block.get("redacted_samples", [])
    if samples:
        lines = ["SAMPLE CONVERSATIONS (redacted):"]
        for i, s in enumerate(samples, 1):
            lines.append(f"  [{i}] {s[:300]}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def replace_prompt_variables(prompt_text, block):
    """Replace {variable} tokens in prompt text with formatted data block values."""
    formatted = format_data_block_for_prompt(block)

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
