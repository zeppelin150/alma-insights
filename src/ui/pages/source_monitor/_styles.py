"""
Shared QSS style constants for the Source Monitor package.

Centralised so all tab files share one canonical look. If you change a
button or field style here, every tab updates. Import the constants
directly — do not redefine them in tab files.
"""

from src.ui.theme import (
    ALMA_BG_ELEVATED,
    ALMA_BORDER,
    ALMA_BORDER_LIGHT,
    ALMA_CREAM,
    ALMA_ERROR,
    ALMA_GREEN_DARK,
    ALMA_GREEN_MID,
    ALMA_TEXT_DARK,
    ALMA_TEXT_LIGHT,
    ALMA_TEXT_MID,
    ALMA_WHITE,
)

#: Card surface — soft sage off-white with a hairline border.
CARD_STYLE = f"""
    QFrame {{
        background: {ALMA_BG_ELEVATED};
        border: 1px solid rgba(214, 210, 202, 0.45);
        border-radius: 12px;
    }}
"""

#: Field style applied to QLineEdit, QSpinBox, and QComboBox in this package.
FIELD_STYLE = f"""
    QLineEdit, QSpinBox, QComboBox {{
        background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
        border-radius: 8px; padding: 8px 12px; font-size: 13px;
        color: {ALMA_TEXT_DARK};
    }}
"""

#: Tiny uppercase form-field label.
LBL_STYLE = (
    f"font-size: 11px; font-weight: 600; "
    f"color: {ALMA_TEXT_MID}; letter-spacing: 0.5px;"
)

#: Secondary "ghost" button — outlined dark green.
GHOST_BTN = f"""
    QPushButton {{
        background: transparent; color: {ALMA_GREEN_DARK};
        border: 1px solid {ALMA_GREEN_DARK}; border-radius: 6px;
        padding: 6px 14px; font-size: 12px; font-weight: 600;
    }}
    QPushButton:hover {{ background: {ALMA_CREAM}; }}
    QPushButton:disabled {{
        color: {ALMA_TEXT_LIGHT}; border-color: {ALMA_BORDER};
    }}
"""

#: Primary action button — dark green filled.
PRIMARY_BTN = f"""
    QPushButton {{
        background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM};
        border: none; border-radius: 8px; padding: 8px 20px;
        font-weight: 600; font-size: 13px;
    }}
    QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
    QPushButton:disabled {{
        background: {ALMA_BORDER}; color: {ALMA_TEXT_LIGHT};
    }}
"""

#: Danger button — red outline.
DANGER_BTN = f"""
    QPushButton {{
        background: transparent; color: {ALMA_ERROR};
        border: 1px solid {ALMA_ERROR}; border-radius: 6px;
        padding: 6px 14px; font-size: 12px; font-weight: 600;
    }}
    QPushButton:hover {{ background: rgba(196,30,30,0.06); }}
"""
