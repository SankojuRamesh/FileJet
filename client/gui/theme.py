"""Modern dark design system (layered neutral surfaces, indigo-blue accent, rounded components).

Colour roles (the keys are used across the app):
  activity  - far-left navigation rail          sidebar - side panels / headers
  editor    - main canvas                       panel   - raised cards
  border    - hairlines                         input   - text fields
  accent    - brand / primary actions           selection / selection_border - selected rows, focus ring
"""

C = {
    # surfaces (darkest -> lightest)
    "activity": "#0d0f13", "sidebar": "#13161b", "editor": "#0f1115", "panel": "#171a21", "raised": "#1d212a",
    "border": "#262a33", "border_strong": "#323845", "input": "#161920", "hover": "#1c2029",
    # text
    "text": "#c9ced8", "strong": "#f1f3f7", "muted": "#7d8595", "faint": "#5a6170",
    # accent
    "accent": "#5b8cff", "accent_hover": "#6f9bff", "accent_press": "#4a79e6", "accent_soft": "#1b2640",
    "button": "#5b8cff", "button_hover": "#6f9bff",
    "selection": "#1d2a45", "selection_border": "#5b8cff",
    # status
    "ok": "#4cc38a", "warn": "#e5b454", "bad": "#f0716b", "info": "#7aa7ff", "folder": "#e3b567",
    "offline_dot": "#5a6170",
    # chrome
    "activity_icon": "#6b7383", "activity_active": "#f1f3f7", "status": "#0d0f13",
}

FONT = "Segoe UI Variable Text"

QSS = f"""
* {{ font-family: "{FONT}", "Segoe UI", "Inter", "Ubuntu", "Noto Sans", sans-serif; font-size: 13px; }}
QWidget {{ color: {C['text']}; }}
QMainWindow, QDialog, QStackedWidget {{ background: {C['editor']}; }}
#LoginDialog {{ background: {C['sidebar']}; }}
QToolTip {{ background: {C['raised']}; color: {C['strong']}; border: 1px solid {C['border_strong']};
            padding: 6px 10px; border-radius: 6px; }}

/* ---------------- navigation rail */
#ActivityBar {{ background: {C['activity']}; border-right: 1px solid {C['border']}; }}
#ActivityBar QToolButton {{ background: transparent; border: none; border-radius: 10px; margin: 3px 8px;
                            width: 40px; height: 40px; }}
#ActivityBar QToolButton:hover {{ background: {C['hover']}; }}
#ActivityBar QToolButton:checked {{ background: {C['accent_soft']}; }}
#Brand {{ color: {C['accent']}; font-size: 18px; font-weight: 700; padding: 12px 0 8px 0; }}

/* ---------------- side panels */
#SideBar {{ background: {C['sidebar']}; }}
#SideTitle {{ color: {C['muted']}; font-size: 11px; font-weight: 700; letter-spacing: 1.2px;
              padding: 16px 12px 8px 18px; }}
#SectionTitle {{ color: {C['muted']}; font-size: 11px; font-weight: 700; letter-spacing: 1.2px; padding: 10px 0 6px 0; }}
#PageTitle {{ color: {C['strong']}; font-size: 22px; font-weight: 600; padding: 4px 0 6px 0; }}
#Crumbs {{ color: {C['muted']}; font-size: 13px; }}
#Muted, QLabel[muted="true"] {{ color: {C['muted']}; }}
#Card {{ background: {C['panel']}; border: 1px solid {C['border']}; border-radius: 12px; }}
#BigId {{ font-family: "Cascadia Mono", Consolas, monospace; font-size: 22px; color: {C['strong']};
          background: {C['raised']}; border: 1px solid {C['border']}; border-radius: 8px; padding: 3px 10px; }}

/* ---------------- status bar */
#StatusBar {{ background: {C['status']}; border-top: 1px solid {C['border']}; }}
#StatusBar QLabel {{ background: transparent; color: {C['muted']}; font-size: 12px; padding: 0 10px; }}
#StatusBar QLabel:hover {{ color: {C['strong']}; background: {C['hover']}; }}

/* ---------------- inputs */
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QDateEdit, QTextEdit {{
    background: {C['input']}; color: {C['strong']}; border: 1px solid {C['border_strong']}; border-radius: 8px;
    padding: 7px 10px; selection-background-color: {C['accent']}; selection-color: #ffffff; }}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover, QPlainTextEdit:hover, QDateEdit:hover {{ border-color: #3d4452; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QDateEdit:focus, QTextEdit:focus {{
    border: 1px solid {C['accent']}; background: #121620; }}
QLineEdit:disabled, QPlainTextEdit:disabled {{ color: {C['faint']}; background: {C['sidebar']}; }}
QComboBox QAbstractItemView {{ background: {C['raised']}; border: 1px solid {C['border_strong']}; border-radius: 8px;
                               padding: 4px; selection-background-color: {C['selection']}; outline: none; }}
QComboBox::drop-down {{ border: none; width: 22px; }}

/* ---------------- buttons */
QPushButton {{ background: {C['accent']}; color: #ffffff; border: 1px solid transparent; border-radius: 8px;
               padding: 8px 16px; font-weight: 600; }}
QPushButton:hover {{ background: {C['accent_hover']}; }}
QPushButton:pressed {{ background: {C['accent_press']}; }}
QPushButton:focus {{ border: 1px solid #9dbaff; }}
QPushButton:disabled {{ background: {C['raised']}; color: {C['faint']}; }}
QPushButton[kind="secondary"] {{ background: {C['raised']}; color: {C['strong']}; border: 1px solid {C['border_strong']}; }}
QPushButton[kind="secondary"]:hover {{ background: #252a35; border-color: #414858; }}
QPushButton[kind="secondary"]:pressed {{ background: #1a1e26; }}
QPushButton[kind="danger"] {{ background: #2a1719; color: #ff8a84; border: 1px solid #4a2427; }}
QPushButton[kind="danger"]:hover {{ background: #3a1c1f; border-color: #6a2f33; }}
QPushButton[kind="secondary"]:disabled, QPushButton[kind="danger"]:disabled {{
    background: {C['sidebar']}; color: {C['faint']}; border: 1px solid {C['border']}; }}
QPushButton[kind="link"] {{ background: transparent; color: {C['info']}; padding: 3px 6px; border: none; font-weight: 500; }}
QPushButton[kind="link"]:hover {{ color: #a9c5ff; }}
QPushButton[kind="link"]:disabled {{ background: transparent; color: {C['faint']}; }}
QPushButton[btn="small"] {{ padding: 4px 12px; font-size: 12px; min-height: 22px; border-radius: 7px; }}
QPushButton:checkable:checked {{ background: {C['accent_soft']}; color: {C['strong']}; border: 1px solid {C['accent']}; }}

QToolButton[kind="tool"] {{ background: transparent; border: none; border-radius: 7px; padding: 6px; }}
QToolButton[kind="tool"]:hover {{ background: {C['hover']}; }}
QToolButton[kind="tool"]:pressed {{ background: #181c24; }}

/* ---------------- lists, trees, tables */
QTableView, QTreeView, QListView {{
    background: {C['panel']}; alternate-background-color: #151820; border: 1px solid {C['border']}; border-radius: 10px;
    gridline-color: transparent; selection-background-color: {C['selection']}; selection-color: {C['strong']};
    outline: none; padding: 2px; }}
#SideBar QTreeView, #SideBar QListView {{ background: {C['sidebar']}; border: none; padding: 4px 8px; }}
QTableView::item {{ padding: 4px 8px; border: none; border-bottom: 1px solid #1c2028; }}
QTreeView::item, QListView::item {{ padding: 6px 8px; border: none; border-radius: 7px; margin: 1px 0; }}
QTableView::item:hover, QTreeView::item:hover, QListView::item:hover {{ background: {C['hover']}; }}
QTableView::item:selected, QTreeView::item:selected, QListView::item:selected {{
    background: {C['selection']}; color: {C['strong']}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: {C['panel']}; color: {C['muted']}; border: none;
                        border-bottom: 1px solid {C['border']}; padding: 9px 8px; font-size: 11px; font-weight: 700;
                        letter-spacing: 0.4px; }}
QHeaderView::section:first {{ border-top-left-radius: 10px; }}
QHeaderView::section:last {{ border-top-right-radius: 10px; }}
QHeaderView::section:hover {{ color: {C['strong']}; }}
QTableCornerButton::section {{ background: {C['panel']}; border: none; }}
QTextBrowser {{ background: {C['panel']}; border: 1px solid {C['border']}; border-radius: 12px; padding: 6px; }}

/* ---------------- scrollbars (thin, overlay-like) */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: #2c313c; min-height: 36px; border-radius: 3px; }}
QScrollBar::handle:vertical:hover {{ background: #3b4250; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 4px; }}
QScrollBar::handle:horizontal {{ background: #2c313c; min-width: 36px; border-radius: 3px; }}
QScrollBar::handle:horizontal:hover {{ background: #3b4250; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---------------- progress */
QProgressBar {{ background: {C['raised']}; border: none; border-radius: 3px; height: 6px; text-align: center;
                color: transparent; max-height: 6px; }}
QProgressBar::chunk {{ background: {C['accent']}; border-radius: 3px; }}
QProgressBar[state="completed"]::chunk {{ background: {C['ok']}; }}
QProgressBar[state="failed"]::chunk {{ background: {C['bad']}; }}
QProgressBar[bar="big"] {{ max-height: 22px; min-height: 22px; height: 22px; color: #ffffff; font-size: 11px;
                           font-weight: 700; border-radius: 11px; background: {C['raised']}; }}
QProgressBar[bar="big"]::chunk {{ border-radius: 11px; }}

/* ---------------- checkboxes */
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid #4a5160; border-radius: 5px; background: {C['input']}; }}
QCheckBox::indicator:hover {{ border-color: {C['accent']}; }}
QCheckBox::indicator:checked {{ background: {C['accent']}; border-color: {C['accent']}; image: url(CHECK_SVG); }}
QRadioButton::indicator {{ width: 16px; height: 16px; }}

/* ---------------- tabs (underline style) */
QTabWidget::pane {{ border: none; border-top: 1px solid {C['border']}; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; color: {C['muted']}; padding: 10px 16px; margin-right: 4px; border: none;
               border-bottom: 2px solid transparent; font-weight: 600; }}
QTabBar::tab:hover {{ color: {C['strong']}; }}
QTabBar::tab:selected {{ color: {C['strong']}; border-bottom: 2px solid {C['accent']}; }}

/* ---------------- misc */
QSplitter::handle {{ background: transparent; }}
QMenu {{ background: {C['raised']}; border: 1px solid {C['border_strong']}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 24px; border-radius: 6px; }}
QMenu::item:selected {{ background: {C['selection']}; }}
QMessageBox {{ background: {C['panel']}; }}
QMessageBox QLabel {{ background: transparent; color: {C['strong']}; }}
QDialog QLabel {{ background: transparent; }}
QFormLayout QLabel {{ color: {C['muted']}; }}
"""
