"""Design tokens — the single source for color, spacing, type, elevation.

`LIGHT` is the canonical palette keyed by semantic role; a future dark
theme drops in as a sibling dict without touching consumers. The flat
`ALMA_*` constants below are the compatibility surface every existing
widget imports (re-exported through `src.ui.theme`), all derived from
`LIGHT` so a palette change cascades everywhere.
"""

LIGHT = {
    # Brand
    "green.dark": "#03281B",
    "green.mid": "#0A3D2C",
    "green.light": "#14573F",
    "green.subtle": "#1B6B4D",
    "accent.teal": "#0D7D72",
    # Surfaces
    "bg.canvas": "#F3F1EC",      # cream app background
    "bg.paper": "#FAFAF8",       # inputs
    "bg.elevated": "#FFFFFF",    # cards
    "bg.inset": "#F7F5F0",       # table headers, wells
    "hover.light": "#EAE7E0",
    # Text
    "text.primary": "#1A1A1A",
    "text.secondary": "#4A4A4A",
    "text.muted": "#7A7A7A",
    "text.on-dark": "#F3F1EC",
    # Borders
    "border.default": "#D6D2CA",
    "border.light": "#E8E5DE",
    # Semantic
    "semantic.success": "#16763A",
    "semantic.warning": "#B45309",
    "semantic.error": "#C41E1E",
    "semantic.info": "#1D6FA5",
    # Shadows (CSS rgba strings)
    "shadow.light": "rgba(0, 0, 0, 0.04)",
    "shadow.med": "rgba(0, 0, 0, 0.08)",
    # Charts
    "chart.bg": "#F2F8F5",
    "chart.grid": "#DCE8E2",
    "chart.axis": "#9A9590",
    "chart.inset": "#E2EDE8",
}

# 4px spacing grid (px)
SPACING = (4, 8, 12, 16, 20, 24, 32)

RADIUS = {"sm": 6, "md": 8, "lg": 10, "xl": 12, "pill": 14}

TYPE_SCALE = {
    "caption": 10, "small": 11, "body": 12, "ui": 13, "subtitle": 14,
    "h3": 16, "h2": 18, "h1": 20, "display": 24, "kpi": 30,
}

ELEVATION = {
    "resting": {"blur": 12, "offset": (0, 2), "alpha": 20},
    "hover": {"blur": 20, "offset": (0, 6), "alpha": 30},
    "soft": {"blur": 10, "offset": (0, 2), "alpha": 18},
}

# ═══ Flat compatibility constants (legacy ALMA_* surface) ═══

ALMA_GREEN_DARK = LIGHT["green.dark"]
ALMA_GREEN_MID = LIGHT["green.mid"]
ALMA_GREEN_LIGHT = LIGHT["green.light"]
ALMA_GREEN_SUBTLE = LIGHT["green.subtle"]
ALMA_CREAM = LIGHT["bg.canvas"]
ALMA_WHITE = LIGHT["bg.paper"]
ALMA_TEXT_DARK = LIGHT["text.primary"]
ALMA_TEXT_MID = LIGHT["text.secondary"]
ALMA_TEXT_LIGHT = LIGHT["text.muted"]
ALMA_TEXT_ON_DARK = LIGHT["text.on-dark"]
ALMA_BORDER = LIGHT["border.default"]
ALMA_BORDER_LIGHT = LIGHT["border.light"]
ALMA_HOVER_LIGHT = LIGHT["hover.light"]
ALMA_SUCCESS = LIGHT["semantic.success"]
ALMA_WARNING = LIGHT["semantic.warning"]
ALMA_ERROR = LIGHT["semantic.error"]
ALMA_INFO = LIGHT["semantic.info"]
ALMA_SHADOW_LIGHT = LIGHT["shadow.light"]
ALMA_SHADOW_MED = LIGHT["shadow.med"]
ALMA_BG_ELEVATED = LIGHT["bg.elevated"]
ALMA_BG_INSET = LIGHT["bg.inset"]
ALMA_CHART_BG = LIGHT["chart.bg"]
ALMA_CHART_GRID = LIGHT["chart.grid"]
ALMA_CHART_AXIS = LIGHT["chart.axis"]
ALMA_CHART_INSET = LIGHT["chart.inset"]
ALMA_ACCENT_TEAL = LIGHT["accent.teal"]

ALMA_CHART_PALETTE = [
    "#14573F",  # Brand green (primary)
    "#1D6FA5",  # Blue
    "#B45309",  # Amber
    "#7C3AED",  # Purple
    "#DB2777",  # Pink
    "#059669",  # Teal
]
