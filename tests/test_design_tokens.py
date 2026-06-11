"""Design tokens — legacy ALMA_* surface equality + scale sanity."""

from src.ui.design import tokens


def test_legacy_constants_derive_from_palette():
    assert tokens.ALMA_GREEN_DARK == tokens.LIGHT["green.dark"]
    assert tokens.ALMA_CREAM == tokens.LIGHT["bg.canvas"]
    assert tokens.ALMA_TEXT_DARK == tokens.LIGHT["text.primary"]
    assert tokens.ALMA_BG_ELEVATED == tokens.LIGHT["bg.elevated"]
    assert tokens.ALMA_SUCCESS == tokens.LIGHT["semantic.success"]


def test_theme_facade_reexports_identical_values():
    from src.ui import theme
    for name in dir(tokens):
        if name.startswith("ALMA_"):
            assert getattr(theme, name) == getattr(tokens, name), name


def test_known_brand_values_unchanged():
    """The redesign must not drift the existing brand palette."""
    assert tokens.ALMA_GREEN_DARK == "#03281B"
    assert tokens.ALMA_CREAM == "#F3F1EC"
    assert tokens.ALMA_BORDER == "#D6D2CA"
    assert tokens.ALMA_ERROR == "#C41E1E"
    assert tokens.ALMA_CHART_PALETTE[0] == "#14573F"
    assert len(tokens.ALMA_CHART_PALETTE) == 6


def test_scales_sane():
    assert list(tokens.SPACING) == sorted(tokens.SPACING)
    assert tokens.SPACING[0] == 4
    assert all(isinstance(v, int) for v in tokens.RADIUS.values())
    assert tokens.TYPE_SCALE["kpi"] == 30
    for level in ("resting", "hover", "soft"):
        spec = tokens.ELEVATION[level]
        assert {"blur", "offset", "alpha"} <= set(spec)
