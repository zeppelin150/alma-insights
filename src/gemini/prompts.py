"""Gemini prompt templates for synthesis and hypothesis testing.

DESIGN PRINCIPLE: Prompts are assembled from AGGREGATED STATISTICS only.
They contain TRC codes, ticket counts, averages, correlation coefficients,
p-values, concept labels, and sentiment scores. They do NOT contain raw
ticket text (full_thread) or patient identifiers.

The only exception is the hypothesis testing prompt, which includes
REDACTED ticket subject lines — these pass through the PII filter in
GeminiClient before being sent.
"""

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "config" / "prompts"

SYSTEM_PROMPT = """You are an RCM analytics assistant for a behavioral
health company. Analyze support ticket data to identify operational
issues, emerging trends, and causal patterns. Be specific, cite
evidence, prioritize actionable insights."""

_DEFAULT_SYNTHESIS_TEMPLATE = """Based on the following RCM support ticket analytics, identify:
1. The most significant emerging issues and why they matter
2. Causal relationships between TRC categories
3. 2-3 actionable recommendations for operations
4. Early warning signals needing immediate attention

DATA:
{data_block}

Cite specific metrics and TRC codes for each finding."""

_DEFAULT_HYPOTHESIS_TEMPLATE = """Evaluate this hypothesis about our RCM operations:

HYPOTHESIS: "{hypothesis}"

EVIDENCE:
{data_block}

1. Does the data SUPPORT or CONTRADICT the hypothesis?
   Rate: Strong / Moderate / Weak / Insufficient Data
2. What specific evidence supports or contradicts it?
3. What additional data would strengthen the finding?
4. What operational action do you recommend?

Be honest about data limitations."""


def _load_template(filename: str, default: str) -> str:
    """Load prompt template from file if it exists, else use default."""
    path = _PROMPTS_DIR / filename
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            pass
    return default


def build_synthesis_prompt(analysis_results: dict) -> str:
    """Feed analysis results to Gemini for narrative synthesis."""
    sections = []

    # Topics
    topics = analysis_results.get("topics", {}).get("topics", [])
    if topics:
        lines = [f"  - {t['label']} ({t['count']} tickets, "
                 f"CSAT: {t.get('avg_csat', 'N/A')}, "
                 f"trend: {t.get('trend_direction', '?')})"
                 for t in topics[:10]]
        sections.append("TOPICS:\n" + "\n".join(lines))

    # Sentiment
    sentiment = analysis_results.get("sentiment", {})
    if sentiment:
        sections.append(f"SENTIMENT: {sentiment.get('overall_trend', 'N/A')}")

    # Rising terms
    rising = analysis_results.get("rising_terms", {}).get("rising", [])
    if rising:
        sections.append("RISING TERMS: " + ", ".join(
            t["term"].replace("_", " ") for t in rising[:10]))

    # Cross-TRC correlations
    corrs = analysis_results.get("correlations", {}).get("correlations", [])
    if corrs:
        lines = [f"  - {c['interpretation']}" for c in corrs[:5]]
        sections.append("CROSS-TRC SIGNALS:\n" + "\n".join(lines))

    # Lead-lag
    leads = analysis_results.get("lead_lag", {}).get("lead_lag_signals", [])
    if leads:
        lines = [f"  - {l['interpretation']}" for l in leads[:5]]
        sections.append("TEMPORAL PATTERNS:\n" + "\n".join(lines))

    # Incident flags (from Pass 1.75)
    incidents = analysis_results.get("incidents", {})
    if incidents:
        flags = incidents.get("new_flags", [])
        if flags:
            lines = [f"  - {f['interpretation']}" for f in flags[:5]]
            sections.append("ACTIVE INCIDENTS:\n" + "\n".join(lines))

    data_block = "\n\n".join(sections)
    template = _load_template("synthesis.txt", _DEFAULT_SYNTHESIS_TEMPLATE)
    return template.format(data_block=data_block)


def build_hypothesis_prompt(hypothesis: str, evidence: dict) -> str:
    """Build prompt for hypothesis testing."""
    sections = []

    tickets = evidence.get("matching_tickets", [])
    if tickets:
        sections.append(f"MATCHING TICKETS: {len(tickets)} found")
        sample = [f"  - [{t['trc_code']}] {t['subject']}" for t in tickets[:15]]
        sections.append("SAMPLES:\n" + "\n".join(sample))

    temporal = evidence.get("temporal_pattern", {})
    if temporal:
        sections.append(f"TEMPORAL: {temporal.get('description', 'N/A')}")

    correlation = evidence.get("correlation", {})
    if correlation:
        sections.append(f"CORRELATION: {correlation.get('interpretation', 'N/A')}")

    incident_data = evidence.get("incident_signals", {})
    if incident_data:
        sections.append(f"INCIDENT FLAGS: {incident_data.get('description', 'N/A')}")

    data_block = "\n\n".join(sections)
    template = _load_template("hypothesis.txt", _DEFAULT_HYPOTHESIS_TEMPLATE)
    return template.format(hypothesis=hypothesis, data_block=data_block)


def build_ab_prompt(ab_data_block: dict) -> str:
    """Build prompt for A/B dataset comparison.

    Uses the ab_comparison.txt template with data from ab_analysis.build_ab_data_block().
    """
    data_text = ab_data_block.get("data_block", "")
    template = _load_template("ab_comparison.txt",
        "Compare these two ticket datasets:\n\n{data_block}\n\n"
        "Identify the top 5 differences, statistical significance, and recommendations."
    )
    return template.format(data_block=data_text)
