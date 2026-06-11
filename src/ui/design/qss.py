"""QSS builder — composes the application stylesheet from tokens.

`_legacy_sections()` is the long-lived stylesheet moved verbatim from
src/ui/theme.py (which now delegates here); `_extras()` carries the
design-system additions (menus, message boxes, icon sizing). New styling
goes in new section functions — do not grow the legacy block.
"""

from src.ui.design.tokens import *  # noqa: F401,F403


def _legacy_sections():
    return f"""
    QWidget {{
        font-family: "Segoe UI", "SF Pro Display", "Helvetica Neue", Arial, sans-serif;
        font-size: 13px;
        color: {ALMA_TEXT_DARK};
    }}
    QMainWindow {{ background-color: {ALMA_CREAM}; }}

    /* ── GLOBAL: Remove visible borders from group boxes ── */
    QGroupBox {{
        border: none;
        margin-top: 8px;
        padding-top: 4px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        padding: 0 6px;
        font-weight: 600;
        color: {ALMA_TEXT_DARK};
    }}

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
        padding: 4px 16px; font-size: 12px; font-weight: 500;
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
        letter-spacing: 1.2px; padding: 20px 20px 8px 20px;
    }}
    #SidebarButton {{
        background: transparent; color: rgba(243,241,236,0.72);
        border: none; border-radius: 8px; padding: 10px 20px;
        text-align: left; font-size: 13px; font-weight: 500; margin: 2px 12px;
    }}
    #SidebarButton:hover {{
        background: {ALMA_GREEN_MID}; color: {ALMA_TEXT_ON_DARK};
    }}
    #SidebarButton[active="true"] {{
        background: {ALMA_GREEN_LIGHT}; color: {ALMA_TEXT_ON_DARK}; font-weight: 600;
        border-left: 3px solid {ALMA_TEXT_ON_DARK};
        padding-left: 17px;
    }}
    /* Collapsed sidebar: icon-only buttons, centered */
    #SidebarButton[collapsed="true"] {{
        padding: 10px 0px; margin: 2px 4px; text-align: center; font-size: 16px;
    }}
    #SidebarButton[collapsed="true"][active="true"] {{
        border-left: none; padding-left: 0px;
        border-bottom: 3px solid {ALMA_TEXT_ON_DARK};
        padding-bottom: 7px;
    }}
    #SidebarDivider {{
        background: rgba(243,241,236,0.1); min-height: 1px; max-height: 1px; margin: 12px 20px;
    }}
    #SidebarDivider[collapsed="true"] {{
        margin: 8px 8px;
    }}
    #SidebarFooter {{
        color: rgba(243,241,236,0.3); font-size: 10px; padding: 12px 20px;
    }}
    #SidebarCollapseBtn {{
        background: transparent; color: rgba(243,241,236,0.4);
        border: none; border-radius: 6px; padding: 8px 20px;
        font-size: 16px; font-weight: 400; margin: 0px 12px;
    }}
    #SidebarCollapseBtn[collapsed="true"] {{
        padding: 8px 0px; margin: 0px 4px; text-align: center;
    }}
    #SidebarCollapseBtn:hover {{
        background: {ALMA_GREEN_MID}; color: {ALMA_TEXT_ON_DARK};
    }}

    /* ── CONTENT ── */
    #ContentArea {{ background-color: {ALMA_CREAM}; }}
    #PageHeader {{
        font-size: 24px; font-weight: 600; color: {ALMA_TEXT_DARK};
        letter-spacing: -0.3px;
    }}
    #PageSubheader {{
        font-size: 13px; color: {ALMA_TEXT_LIGHT}; margin-bottom: 4px;
    }}

    /* ── SECTION LABELS & HEADINGS ── */
    #SectionLabel {{
        font-size: 11px; font-weight: 700; letter-spacing: 0.8px;
        color: {ALMA_TEXT_LIGHT}; text-transform: uppercase;
    }}
    #SectionHeading {{
        font-size: 18px; font-weight: 600; color: {ALMA_TEXT_DARK};
        letter-spacing: -0.2px;
    }}

    /* ── CARDS ── */
    #Card {{
        background-color: {ALMA_BG_ELEVATED};
        border: none;
        border-radius: 12px;
    }}
    #CardTitle {{ font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; }}

    /* ── INPUTS ── (T5: 4px grid — 10px 16px) */
    QLineEdit {{
        background: {ALMA_WHITE};
        border: 1px solid rgba(214, 210, 202, 0.7);
        border-radius: 8px;
        padding: 10px 16px; font-size: 13px; color: {ALMA_TEXT_DARK};
        selection-background-color: {ALMA_GREEN_LIGHT}; selection-color: white;
    }}
    QLineEdit:focus {{
        border-color: {ALMA_GREEN_LIGHT}; border-width: 2px; padding: 9px 15px;
    }}

    QTextEdit, QPlainTextEdit {{
        background: {ALMA_WHITE};
        border: 1px solid rgba(214, 210, 202, 0.7);
        border-radius: 8px;
        padding: 10px 16px; font-size: 13px; color: {ALMA_TEXT_DARK};
    }}
    QTextEdit:focus {{ border-color: {ALMA_GREEN_LIGHT}; }}

    QComboBox {{
        background: {ALMA_WHITE};
        border: 1px solid rgba(214, 210, 202, 0.7);
        border-radius: 8px;
        padding: 10px 16px; font-size: 13px; color: {ALMA_TEXT_DARK}; min-width: 120px;
    }}
    QComboBox:hover {{ border-color: {ALMA_GREEN_LIGHT}; }}
    QComboBox:focus {{ border-color: {ALMA_GREEN_LIGHT}; border-width: 2px; }}
    QComboBox::drop-down {{
        subcontrol-origin: padding; subcontrol-position: center right;
        width: 32px; border: none;
    }}
    QComboBox QAbstractItemView {{
        background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER}; border-radius: 6px;
        padding: 4px; color: {ALMA_TEXT_DARK};
        selection-background-color: {ALMA_GREEN_LIGHT}; selection-color: white;
    }}

    QDateEdit {{
        background: {ALMA_WHITE};
        border: 1px solid rgba(214, 210, 202, 0.7);
        border-radius: 8px;
        padding: 10px 16px; font-size: 13px; color: {ALMA_TEXT_DARK};
    }}
    QDateEdit:focus {{ border-color: {ALMA_GREEN_LIGHT}; }}

    /* ── DATE PICKER BUTTON ── */
    #DatePickerButton {{
        background: {ALMA_WHITE};
        border: 1px solid rgba(214, 210, 202, 0.7);
        border-radius: 8px;
        padding: 10px 16px; font-size: 13px; color: {ALMA_TEXT_DARK};
        text-align: left;
    }}
    #DatePickerButton:hover {{ border-color: {ALMA_GREEN_LIGHT}; }}
    #DatePickerButton:focus {{ border-color: {ALMA_GREEN_LIGHT}; border-width: 2px; }}

    /* ── BUTTONS ── (T5: 4px grid — 10px 24px) */
    QPushButton {{
        background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK}; border: none;
        border-radius: 8px; padding: 10px 24px; font-size: 13px; font-weight: 600;
    }}
    QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
    QPushButton:pressed {{ background: {ALMA_GREEN_LIGHT}; }}
    QPushButton:disabled {{ background: {ALMA_BORDER}; color: {ALMA_TEXT_LIGHT}; }}
    QPushButton:focus {{
        outline: none; border: 2px solid {ALMA_GREEN_LIGHT};
    }}

    #SecondaryButton {{
        background: transparent; color: {ALMA_GREEN_DARK};
        border: 1px solid rgba(214, 210, 202, 0.7);
        border-radius: 8px; padding: 10px 24px; font-weight: 500;
    }}
    #SecondaryButton:hover {{ background: {ALMA_HOVER_LIGHT}; border-color: {ALMA_GREEN_LIGHT}; }}

    #GhostButton {{
        background: transparent; color: {ALMA_TEXT_MID}; border: none;
        padding: 8px 12px; font-weight: 500;
    }}
    #GhostButton:hover {{ color: {ALMA_GREEN_DARK}; background: {ALMA_HOVER_LIGHT}; border-radius: 6px; }}

    /* ── INCIDENT CARD BUTTONS ── (shared, not per-card QSS) */
    #IncidentAckBtn {{
        background: transparent; color: {ALMA_INFO};
        border: 1px solid {ALMA_INFO}; border-radius: 6px;
        padding: 4px 12px; font-size: 11px; font-weight: 500;
    }}
    #IncidentAckBtn:hover {{ background: rgba(29,111,165,0.08); }}

    #IncidentResolveBtn {{
        background: transparent; color: {ALMA_SUCCESS};
        border: 1px solid {ALMA_SUCCESS}; border-radius: 6px;
        padding: 4px 12px; font-size: 11px; font-weight: 500;
    }}
    #IncidentResolveBtn:hover {{ background: rgba(22,118,58,0.08); }}

    #IncidentFPBtn {{
        background: transparent; color: {ALMA_TEXT_LIGHT};
        border: 1px solid {ALMA_BORDER}; border-radius: 6px;
        padding: 4px 12px; font-size: 11px; font-weight: 500;
    }}
    #IncidentFPBtn:hover {{ background: rgba(0,0,0,0.04); }}

    #IncidentViewBtn {{
        background: transparent; color: {ALMA_GREEN_DARK};
        border: 1px solid {ALMA_GREEN_LIGHT}; border-radius: 6px;
        padding: 4px 12px; font-size: 11px; font-weight: 500;
    }}
    #IncidentViewBtn:hover {{ background: rgba(20,87,63,0.08); }}

    #IncidentPageBtn {{
        background: transparent; color: {ALMA_GREEN_DARK};
        border: 1px solid {ALMA_BORDER}; border-radius: 6px;
        padding: 4px 12px; font-size: 11px; font-weight: 500;
    }}
    #IncidentPageBtn:hover {{ background: {ALMA_HOVER_LIGHT}; }}

    /* ── TABLES ── (T5: 4px grid — 10px 16px) */
    QTableWidget, QTableView {{
        background: {ALMA_BG_ELEVATED};
        border: none;
        border-radius: 10px;
        gridline-color: rgba(214, 210, 202, 0.35);
        selection-background-color: {ALMA_GREEN_SUBTLE};
        selection-color: {ALMA_TEXT_ON_DARK};
        outline: 0;
    }}
    QTableWidget::item {{
        padding: 10px 16px;
        border-bottom: 1px solid rgba(214, 210, 202, 0.3);
        outline: 0;
    }}
    QTableWidget::item:selected {{
        background: {ALMA_GREEN_SUBTLE};
        color: {ALMA_TEXT_ON_DARK};
    }}
    QTableWidget::item:focus {{
        background: {ALMA_GREEN_SUBTLE};
        color: {ALMA_TEXT_ON_DARK};
        border: none;
        outline: 0;
    }}
    QTableWidget::item:hover {{
        background: rgba(3, 40, 27, 0.04);
    }}

    /* ── TREE WIDGETS ── */
    QTreeWidget {{
        selection-background-color: {ALMA_GREEN_SUBTLE};
        selection-color: {ALMA_TEXT_ON_DARK};
        outline: 0;
    }}
    QTreeWidget::item {{
        outline: 0;
    }}
    QTreeWidget::item:selected {{
        background: {ALMA_GREEN_SUBTLE};
        color: {ALMA_TEXT_ON_DARK};
    }}
    QTreeWidget::item:focus {{
        background: {ALMA_GREEN_SUBTLE};
        color: {ALMA_TEXT_ON_DARK};
        border: none;
        outline: 0;
    }}
    QTreeWidget::item:hover {{
        background: rgba(3, 40, 27, 0.04);
    }}

    QHeaderView::section {{
        background: {ALMA_BG_INSET}; color: {ALMA_TEXT_MID};
        font-size: 11px; font-weight: 600; padding: 10px 16px;
        border: none; border-bottom: 2px solid rgba(214, 210, 202, 0.5);
        text-transform: uppercase; letter-spacing: 0.5px;
    }}

    /* ── SCROLLBARS ── */
    QScrollBar:vertical {{ background: transparent; width: 8px; }}
    QScrollBar::handle:vertical {{ background: {ALMA_BORDER}; min-height: 32px; border-radius: 4px; }}
    QScrollBar::handle:vertical:hover {{ background: {ALMA_TEXT_LIGHT}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
    QScrollBar:horizontal {{ background: transparent; height: 8px; }}
    QScrollBar::handle:horizontal {{ background: {ALMA_BORDER}; min-width: 32px; border-radius: 4px; }}

    /* ── TABS ── */
    QTabWidget::pane {{
        border: none; border-radius: 12px;
        background: {ALMA_BG_ELEVATED}; top: -1px;
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
        border-radius: 6px; padding: 8px 12px; font-size: 12px;
    }}

    QSplitter::handle {{ background: {ALMA_BORDER_LIGHT}; width: 1px; }}

    QProgressBar {{
        background: {ALMA_BORDER_LIGHT}; border: none; border-radius: 4px; height: 6px;
    }}
    QProgressBar::chunk {{ background: {ALMA_GREEN_LIGHT}; border-radius: 4px; }}

    QDialog {{ background: {ALMA_CREAM}; }}

    /* ── BADGES (T1: full palette) ── */
    #BadgeGreen {{
        background: rgba(22,118,58,0.12); color: {ALMA_SUCCESS};
        border-radius: 10px; padding: 3px 10px; font-size: 11px; font-weight: 600;
    }}
    #BadgeAmber {{
        background: rgba(180,83,9,0.12); color: {ALMA_WARNING};
        border-radius: 10px; padding: 3px 10px; font-size: 11px; font-weight: 600;
    }}
    #BadgeRed {{
        background: rgba(196,30,30,0.12); color: {ALMA_ERROR};
        border-radius: 10px; padding: 3px 10px; font-size: 11px; font-weight: 600;
    }}
    #BadgeBlue {{
        background: rgba(29,111,165,0.12); color: {ALMA_INFO};
        border-radius: 10px; padding: 3px 10px; font-size: 11px; font-weight: 600;
    }}
    #BadgeGray {{
        background: rgba(0,0,0,0.06); color: {ALMA_TEXT_LIGHT};
        border-radius: 10px; padding: 3px 10px; font-size: 11px; font-weight: 600;
    }}

    /* ── KPI CARDS (T2) ── */
    #KPIValue {{
        font-size: 30px; font-weight: 700; color: {ALMA_TEXT_DARK};
        letter-spacing: -0.5px;
    }}
    #KPILabel {{
        font-size: 12px; font-weight: 600; color: {ALMA_TEXT_LIGHT};
        letter-spacing: 0.5px; text-transform: uppercase;
    }}
    #KPIDeltaPositive {{
        font-size: 12px; font-weight: 600; color: {ALMA_SUCCESS};
    }}
    #KPIDeltaNegative {{
        font-size: 12px; font-weight: 600; color: {ALMA_ERROR};
    }}
    #KPIDeltaNeutral {{
        font-size: 12px; font-weight: 600; color: {ALMA_TEXT_LIGHT};
    }}

    /* ── FILTER CHIPS (T3) ── */
    #FilterChip {{
        background: rgba(3,40,27,0.08); color: {ALMA_GREEN_DARK};
        border: none; border-radius: 14px;
        padding: 4px 12px; font-size: 12px; font-weight: 500;
    }}
    #FilterChip:hover {{
        background: rgba(3,40,27,0.12);
    }}

    /* ── EMPTY STATES (T3) ── */
    #EmptyStateIcon {{
        font-size: 48px; color: {ALMA_BORDER};
    }}
    #EmptyStateTitle {{
        font-size: 18px; font-weight: 600; color: {ALMA_TEXT_DARK};
    }}
    #EmptyStateDescription {{
        font-size: 13px; color: {ALMA_TEXT_LIGHT};
    }}

    /* ── TOAST NOTIFICATIONS (T9 placeholder) ── */
    #Toast {{
        background: {ALMA_BG_ELEVATED};
        border: none;
        border-radius: 10px;
    }}
    #ToastSuccess {{ border-left: 4px solid {ALMA_SUCCESS}; }}
    #ToastError {{ border-left: 4px solid {ALMA_ERROR}; }}
    #ToastInfo {{ border-left: 4px solid {ALMA_INFO}; }}
    #ToastWarning {{ border-left: 4px solid {ALMA_WARNING}; }}

    /* ── BUILDING BLOCKS (Analysis Page Base) ── */
    #AnalysisTab::pane {{
        border: none; border-radius: 12px;
        background: {ALMA_CREAM}; top: -1px;
    }}
    /* v2: Green-cell active tabs — only the selected tab gets a filled cell */
    #AnalysisTab > QTabBar::tab {{
        padding: 10px 24px; font-size: 13px; font-weight: 500;
        border: none; border-bottom: 2px solid transparent;
        background: transparent; color: {ALMA_TEXT_MID};
        border-radius: 8px 8px 0px 0px; margin: 0px 2px;
    }}
    #AnalysisTab > QTabBar::tab:selected {{
        background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK};
        font-weight: 600; border-bottom: 2px solid {ALMA_GREEN_DARK};
    }}
    #AnalysisTab > QTabBar::tab:hover:!selected {{
        background: rgba(3, 40, 27, 0.08); color: {ALMA_TEXT_DARK};
    }}
    #FilterBar {{
        background: {ALMA_BG_ELEVATED};
        border: none;
        border-radius: 10px;
    }}
    /* v2: Filter bar separator between action and filters */
    #FilterSep {{
        background: {ALMA_BORDER}; min-width: 1px; max-width: 1px;
        margin: 8px 4px;
    }}
    #KPICard {{
        background: {ALMA_BG_ELEVATED};
        border: none;
        border-radius: 12px;
    }}
    /* v2: KPI accent border colors */
    #KPICard[accent="green"]  {{ border-left: 4px solid {ALMA_GREEN_LIGHT}; }}
    #KPICard[accent="blue"]   {{ border-left: 4px solid {ALMA_INFO}; }}
    #KPICard[accent="teal"]   {{ border-left: 4px solid #0D9488; }}
    #KPICard[accent="amber"]  {{ border-left: 4px solid {ALMA_WARNING}; }}
    """


def _extras():
    return f"""
    /* ── Design-system additions (P3) ── */
    QMenu {{
        background: {ALMA_BG_ELEVATED}; border: 1px solid {ALMA_BORDER};
        border-radius: 8px; padding: 6px;
    }}
    QMenu::item {{
        padding: 8px 16px; border-radius: 6px; color: {ALMA_TEXT_DARK};
        font-size: 12px;
    }}
    QMenu::item:selected {{ background: {ALMA_HOVER_LIGHT}; }}
    QMenu::separator {{
        height: 1px; background: {ALMA_BORDER_LIGHT}; margin: 4px 8px;
    }}
    QMessageBox {{ background: {ALMA_CREAM}; }}
    QCheckBox:focus, QRadioButton:focus {{ outline: none; }}
    /* Sidebar nav buttons carry QIcons since the icon-system pass */
    #SidebarButton {{ icon-size: 18px; }}
    #SidebarCollapseBtn {{ icon-size: 16px; }}
    """


def build_stylesheet(theme=None):
    """Compose the full app stylesheet.

    `theme` is reserved for the future dark variant — tokens are already
    keyed through one palette dict, so the variant swaps there.
    """
    return _legacy_sections() + _extras()
