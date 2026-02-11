"""
Alma Insights — Brand Theme & Stylesheet
Colors extracted from official Alma logo.
"""

# ═══ ALMA BRAND COLORS ═══
ALMA_GREEN_DARK = "#03281B"
ALMA_GREEN_MID = "#0A3D2C"
ALMA_GREEN_LIGHT = "#14573F"
ALMA_GREEN_SUBTLE = "#1B6B4D"
ALMA_CREAM = "#F3F1EC"
ALMA_WHITE = "#FAFAF8"
ALMA_TEXT_DARK = "#1A1A1A"
ALMA_TEXT_MID = "#4A4A4A"
ALMA_TEXT_LIGHT = "#7A7A7A"
ALMA_TEXT_ON_DARK = "#F3F1EC"
ALMA_BORDER = "#D6D2CA"
ALMA_BORDER_LIGHT = "#E8E5DE"
ALMA_HOVER_LIGHT = "#EAE7E0"
ALMA_SUCCESS = "#16763A"
ALMA_WARNING = "#B45309"
ALMA_ERROR = "#C41E1E"
ALMA_INFO = "#1D6FA5"


def get_stylesheet():
    return f"""
    QWidget {{
        font-family: "Segoe UI", "SF Pro Display", "Helvetica Neue", Arial, sans-serif;
        font-size: 13px;
        color: {ALMA_TEXT_DARK};
    }}
    QMainWindow {{ background-color: {ALMA_CREAM}; }}

    /* ── TOP BAR ── */
    #TopBar {{
        background-color: {ALMA_GREEN_DARK};
        min-height: 54px; max-height: 54px;
    }}
    #TopBarTitle {{
        color: {ALMA_TEXT_ON_DARK}; font-size: 20px; font-weight: 600; letter-spacing: 0.5px;
    }}
    #TopBarSubtitle {{
        color: rgba(243,241,236,0.55); font-size: 11px;
    }}
    #TopBarButton {{
        background: transparent; color: {ALMA_TEXT_ON_DARK};
        border: 1px solid rgba(243,241,236,0.25); border-radius: 6px;
        padding: 5px 14px; font-size: 12px; font-weight: 500;
    }}
    #TopBarButton:hover {{
        background: rgba(243,241,236,0.12); border-color: rgba(243,241,236,0.4);
    }}

    /* ── SIDEBAR ── */
    #Sidebar {{
        background-color: {ALMA_GREEN_DARK}; min-width: 220px; max-width: 220px;
    }}
    #SidebarSection {{
        color: rgba(243,241,236,0.4); font-size: 10px; font-weight: 700;
        letter-spacing: 1.2px; padding: 16px 20px 6px 20px;
    }}
    #SidebarButton {{
        background: transparent; color: rgba(243,241,236,0.72);
        border: none; border-radius: 8px; padding: 10px 20px;
        text-align: left; font-size: 13px; font-weight: 500; margin: 1px 10px;
    }}
    #SidebarButton:hover {{
        background: {ALMA_GREEN_MID}; color: {ALMA_TEXT_ON_DARK};
    }}
    #SidebarButton[active="true"] {{
        background: {ALMA_GREEN_LIGHT}; color: {ALMA_TEXT_ON_DARK}; font-weight: 600;
    }}
    #SidebarDivider {{
        background: rgba(243,241,236,0.1); min-height: 1px; max-height: 1px; margin: 8px 20px;
    }}
    #SidebarFooter {{
        color: rgba(243,241,236,0.3); font-size: 10px; padding: 10px 20px;
    }}

    /* ── CONTENT ── */
    #ContentArea {{ background-color: {ALMA_CREAM}; }}
    #PageHeader {{ font-size: 22px; font-weight: 700; color: {ALMA_TEXT_DARK}; }}
    #PageSubheader {{ font-size: 13px; color: {ALMA_TEXT_MID}; }}

    /* ── CARDS ── */
    #Card {{
        background-color: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
        border-radius: 10px;
    }}
    #CardTitle {{ font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; }}

    /* ── INPUTS ── */
    QLineEdit {{
        background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER}; border-radius: 8px;
        padding: 8px 12px; font-size: 13px; color: {ALMA_TEXT_DARK};
        selection-background-color: {ALMA_GREEN_LIGHT}; selection-color: white;
    }}
    QLineEdit:focus {{ border-color: {ALMA_GREEN_LIGHT}; border-width: 2px; padding: 7px 11px; }}

    QTextEdit, QPlainTextEdit {{
        background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER}; border-radius: 8px;
        padding: 10px 12px; font-size: 13px; color: {ALMA_TEXT_DARK};
    }}
    QTextEdit:focus {{ border-color: {ALMA_GREEN_LIGHT}; }}

    QComboBox {{
        background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER}; border-radius: 8px;
        padding: 8px 12px; font-size: 13px; min-width: 120px;
    }}
    QComboBox:hover {{ border-color: {ALMA_GREEN_LIGHT}; }}
    QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 30px; border: none; }}
    QComboBox QAbstractItemView {{
        background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER}; border-radius: 6px;
        padding: 4px; selection-background-color: {ALMA_GREEN_LIGHT}; selection-color: white;
    }}

    QDateEdit {{
        background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER}; border-radius: 8px;
        padding: 8px 12px; font-size: 13px;
    }}
    QDateEdit:focus {{ border-color: {ALMA_GREEN_LIGHT}; }}

    /* ── BUTTONS ── */
    QPushButton {{
        background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK}; border: none;
        border-radius: 8px; padding: 9px 20px; font-size: 13px; font-weight: 600;
    }}
    QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
    QPushButton:pressed {{ background: {ALMA_GREEN_LIGHT}; }}
    QPushButton:disabled {{ background: {ALMA_BORDER}; color: {ALMA_TEXT_LIGHT}; }}

    #SecondaryButton {{
        background: transparent; color: {ALMA_GREEN_DARK};
        border: 1px solid {ALMA_BORDER}; border-radius: 8px; padding: 8px 18px; font-weight: 500;
    }}
    #SecondaryButton:hover {{ background: {ALMA_HOVER_LIGHT}; border-color: {ALMA_GREEN_LIGHT}; }}

    #GhostButton {{
        background: transparent; color: {ALMA_TEXT_MID}; border: none;
        padding: 6px 12px; font-weight: 500;
    }}
    #GhostButton:hover {{ color: {ALMA_GREEN_DARK}; background: {ALMA_HOVER_LIGHT}; border-radius: 6px; }}

    /* ── TABLES ── */
    QTableWidget, QTableView {{
        background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
        border-radius: 8px; gridline-color: {ALMA_BORDER_LIGHT};
        selection-background-color: rgba(3,40,27,0.08);
    }}
    QTableWidget::item {{ padding: 8px 12px; border-bottom: 1px solid {ALMA_BORDER_LIGHT}; }}
    QHeaderView::section {{
        background: {ALMA_CREAM}; color: {ALMA_TEXT_MID};
        font-size: 11px; font-weight: 700; padding: 10px 12px;
        border: none; border-bottom: 2px solid {ALMA_BORDER};
    }}

    /* ── SCROLLBARS ── */
    QScrollBar:vertical {{ background: transparent; width: 8px; }}
    QScrollBar::handle:vertical {{ background: {ALMA_BORDER}; min-height: 30px; border-radius: 4px; }}
    QScrollBar::handle:vertical:hover {{ background: {ALMA_TEXT_LIGHT}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
    QScrollBar:horizontal {{ background: transparent; height: 8px; }}
    QScrollBar::handle:horizontal {{ background: {ALMA_BORDER}; min-width: 30px; border-radius: 4px; }}

    /* ── TABS ── */
    QTabWidget::pane {{
        border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 8px;
        background: {ALMA_WHITE}; top: -1px;
    }}
    QTabBar::tab {{
        background: transparent; color: {ALMA_TEXT_MID}; padding: 10px 20px;
        font-size: 13px; font-weight: 500; border: none; border-bottom: 2px solid transparent;
    }}
    QTabBar::tab:selected {{ color: {ALMA_GREEN_DARK}; border-bottom-color: {ALMA_GREEN_DARK}; font-weight: 600; }}
    QTabBar::tab:hover:!selected {{ color: {ALMA_TEXT_DARK}; border-bottom-color: {ALMA_BORDER}; }}

    /* ── STATUS BAR ── */
    QStatusBar {{
        background: {ALMA_CREAM}; color: {ALMA_TEXT_LIGHT}; font-size: 11px;
        border-top: 1px solid {ALMA_BORDER_LIGHT}; padding: 4px 12px;
    }}

    QToolTip {{
        background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK}; border: none;
        border-radius: 6px; padding: 6px 10px; font-size: 12px;
    }}

    QSplitter::handle {{ background: {ALMA_BORDER_LIGHT}; width: 1px; }}

    QProgressBar {{
        background: {ALMA_BORDER_LIGHT}; border: none; border-radius: 4px; height: 6px;
    }}
    QProgressBar::chunk {{ background: {ALMA_GREEN_LIGHT}; border-radius: 4px; }}

    QDialog {{ background: {ALMA_CREAM}; }}

    /* ── BADGES ── */
    #BadgeGreen {{
        background: rgba(22,118,58,0.12); color: {ALMA_SUCCESS};
        border-radius: 10px; padding: 3px 10px; font-size: 11px; font-weight: 600;
    }}
    #BadgeAmber {{
        background: rgba(180,83,9,0.12); color: {ALMA_WARNING};
        border-radius: 10px; padding: 3px 10px; font-size: 11px; font-weight: 600;
    }}
    """
