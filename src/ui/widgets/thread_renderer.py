"""
Alma Insights — Shared Thread Renderer
Converts raw conversation thread text into styled HTML, and builds
metadata strings from conversation dicts.  Used by the drill-down panel
and the conversations page.
"""

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_GREEN_SUBTLE,
    ALMA_WHITE, ALMA_TEXT_DARK, ALMA_TEXT_LIGHT,
)

# Role styling: CUSTOMER (light green), AGENT (dark green), BOT (grey)
_ROLE_STYLES = {
    "CUSTOMER": {
        "bg": "#F0F8F5",
        "border": ALMA_GREEN_SUBTLE,
        "color": ALMA_GREEN_SUBTLE,
        "label": "CUSTOMER",
    },
    "AGENT": {
        "bg": ALMA_WHITE,
        "border": ALMA_GREEN_LIGHT,
        "color": ALMA_GREEN_DARK,
        "label": "AGENT",
    },
    "BOT": {
        "bg": "#F5F5F5",
        "border": ALMA_TEXT_LIGHT,
        "color": ALMA_TEXT_LIGHT,
        "label": "BOT",
    },
}


def render_thread_html(thread_text: str) -> str:
    """Convert raw thread text (messages separated by \\n\\n---\\n\\n) into
    styled HTML with role-coloured message cards."""
    messages = thread_text.split("\n\n---\n\n")
    html_parts = []

    for msg in messages:
        lines = msg.strip().split("\n", 1)
        if len(lines) < 2:
            continue

        header = lines[0]
        body = lines[1] if len(lines) > 1 else ""

        # Detect role from header text
        header_upper = header.upper()
        if "CUSTOMER" in header_upper or "CLIENT" in header_upper or "END-USER" in header_upper:
            style = _ROLE_STYLES["CUSTOMER"]
        elif "BOT" in header_upper:
            style = _ROLE_STYLES["BOT"]
        else:
            style = _ROLE_STYLES["AGENT"]

        # Extract timestamp and name from header
        # Format: [2025-01-15 14:30] CUSTOMER (Sarah M.):
        timestamp = ""
        name = ""
        if "]" in header:
            timestamp = header.split("]")[0].replace("[", "").strip()
        if "(" in header and ")" in header:
            name = header.split("(")[1].split(")")[0]

        html_parts.append(f"""
            <div style="margin-bottom: 16px; padding: 12px 16px;
                        background: {style['bg']}; border-left: 3px solid {style['border']};
                        border-radius: 0 8px 8px 0;">
                <div style="margin-bottom: 6px;">
                    <span style="font-size: 11px; font-weight: 700; color: {style['color']};
                                 letter-spacing: 0.5px;">{style['label']}</span>
                    <span style="font-size: 11px; color: {ALMA_TEXT_LIGHT};
                                 margin-left: 8px;">{name}</span>
                    <span style="font-size: 11px; color: {ALMA_TEXT_LIGHT};
                                 float: right;">{timestamp}</span>
                </div>
                <div style="font-size: 13px; color: {ALMA_TEXT_DARK}; line-height: 1.65;">
                    {body.replace(chr(10), '<br/>')}
                </div>
            </div>
        """)

    if not html_parts:
        return f"<p style='color: {ALMA_TEXT_LIGHT};'>No conversation data available.</p>"

    return f"""
    <div style="font-family: 'Segoe UI', Arial, sans-serif; padding: 4px;">
        {''.join(html_parts)}
    </div>
    """


def build_meta_text(conv: dict) -> str:
    """Build a dot-separated metadata string from a conversation dict."""
    parts = []
    if conv.get("trc_code"):
        label = conv.get("trc_label", "")
        parts.append(f"TRC: {conv['trc_code']}" + (f" — {label}" if label else ""))
    if conv.get("status"):
        parts.append(f"Status: {conv['status'].title()}")
    if conv.get("csat_score"):
        parts.append(f"CSAT: {int(conv['csat_score'])}/5")
    if conv.get("message_count"):
        customer_n = conv.get("client_messages", 0)
        agent_n = conv.get("agent_messages", 0)
        total_n = conv.get("message_count", 0)
        bot_n = max(0, total_n - customer_n - agent_n)
        sub = []
        if customer_n:
            sub.append(f"{customer_n} customer")
        if agent_n:
            sub.append(f"{agent_n} agent")
        if bot_n:
            sub.append(f"{bot_n} bot")
        parts.append(f"Messages: {total_n} ({', '.join(sub)})" if sub else f"Messages: {total_n}")
    if conv.get("created_at"):
        parts.append(f"Created: {conv['created_at'][:10]}")
    if conv.get("solved_at"):
        parts.append(f"Solved: {conv['solved_at'][:10]}")
    return "  •  ".join(parts)
