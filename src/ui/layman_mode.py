"""
Alma Insights — Layman Mode Translation Layer
Replaces technical metric labels with plain-language equivalents.
Controlled by the 'Simplified Language Mode' toggle in Settings.
"""

from src.data.settings_manager import get_section


def is_layman_mode() -> bool:
    """Check if Simplified Language Mode is enabled in settings."""
    try:
        display = get_section("display", {})
        return bool(display.get("layman_mode", False))
    except Exception:
        pass
    return False


LAYMAN_TRANSLATIONS = {
    # Headers / Labels
    "VADER Compound": "Customer Mood Score",
    "Sentiment Compound": "Customer Mood",
    "CUSUM": "Gradual Change Detector",
    "Poisson \u03bb": "Expected Daily Volume",
    "TF-IDF Score": "Term Importance",
    "z-score": "Unusual Activity Score",
    "NMF Topic": "Auto-Detected Theme",
    "p-value": "Statistical Confidence",
    "\u0394 Change": "Change",
    "Cross-TRC Correlation": "Related Issue Patterns",
    "EWMA": "Smart Average",

    # Status labels
    "Improving": "Getting Better",
    "Declining": "Getting Worse",
    "Stable": "No Change",
    "Flagged": "Needs Attention",

    # Column headers
    "\u03bb": "Expected",
    "\u03b8\u2081": "Warning",
    "\u03b8\u2082": "Critical",
}


def translate_label(text: str, layman_mode: bool) -> str:
    """Replace technical labels with plain language if layman mode is on."""
    if not layman_mode:
        return text
    for technical, plain in LAYMAN_TRANSLATIONS.items():
        text = text.replace(technical, plain)
    return text


def format_sentiment(compound: float, layman_mode: bool) -> str:
    """Format a VADER compound score for display."""
    if not layman_mode:
        return f"{compound:+.2f}"

    abs_val = abs(compound)
    pct = int(abs_val * 100)
    if compound > 0.2:
        return f"Positive ({pct}%)"
    elif compound > 0.05:
        return f"Slightly Positive ({pct}%)"
    elif compound > -0.05:
        return "Neutral"
    elif compound > -0.2:
        return f"Slightly Negative ({pct}%)"
    else:
        return f"Negative ({pct}%)"


def format_pvalue(p: float, layman_mode: bool) -> str:
    """Format a p-value for display."""
    if not layman_mode:
        return f"p={p:.4f}"

    if p < 0.001:
        return "Very high confidence"
    elif p < 0.01:
        return "High confidence"
    elif p < 0.05:
        return "Moderate confidence"
    else:
        return "Low confidence"


def format_lambda(lam: float, layman_mode: bool) -> str:
    """Format a Poisson lambda for display."""
    if not layman_mode:
        return f"\u03bb={lam:.1f}"
    return f"~{lam:.0f} tickets/day expected"


def format_zscore(z: float, layman_mode: bool) -> str:
    """Format a z-score for display."""
    if not layman_mode:
        return f"z={z:.1f}"

    if z > 4:
        return "Extremely unusual"
    elif z > 3:
        return "Very unusual"
    elif z > 2:
        return "Somewhat unusual"
    else:
        return "Normal range"


def format_tfidf(score: float, layman_mode: bool) -> str:
    """Format a TF-IDF score for display."""
    if not layman_mode:
        return f"{score:.3f}"

    if score > 0.05:
        return "Very important"
    elif score > 0.02:
        return "Important"
    elif score > 0.01:
        return "Moderate"
    else:
        return "Low importance"


def format_cusum(value: float, threshold: float, layman_mode: bool) -> str:
    """Format a CUSUM value for display."""
    if not layman_mode:
        return f"{value:.1f} / {threshold:.0f}"

    ratio = value / threshold if threshold > 0 else 0
    if ratio > 0.8:
        return "Sustained high volume detected"
    elif ratio > 0.5:
        return "Moderate sustained increase"
    elif ratio > 0.2:
        return "Slight uptick"
    else:
        return "Normal pattern"


def format_theta_level(theta: int, layman_mode: bool) -> str:
    """Format a theta level (1 or 2) for display."""
    if not layman_mode:
        return f"{theta}\u03b8"

    if theta >= 2:
        return "Critical"
    elif theta >= 1:
        return "Warning"
    else:
        return "Normal"
