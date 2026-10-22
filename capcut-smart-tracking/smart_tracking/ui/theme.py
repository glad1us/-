"""Dark graphite theme."""

BG = "#1b1d21"
PANEL = "#23262b"
PANEL_2 = "#2a2e34"
BORDER = "#363b43"
TEXT = "#e3e6ea"
TEXT_DIM = "#9aa1ab"
ACCENT = "#2fd3c4"      # teal, close to CapCut's accent family
ACCENT_DARK = "#1fa396"
WARN = "#f2b84b"
ERROR = "#f0605d"
OK = "#4cc97a"

STATUS_COLORS = {
    0: "#3a3f47",   # none
    1: OK,          # tracked
    2: ERROR,       # lost
    3: WARN,        # manual
    4: "#4aa8ff",   # reacquired
}

QSS = f"""
* {{ font-family: "Segoe UI", "Inter", "Noto Sans", sans-serif; font-size: 12px; color: {TEXT}; }}
QMainWindow, QWidget#Root {{ background: {BG}; }}
QWidget#Panel {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; }}
QWidget#Inner {{ background: {PANEL}; }}
QLabel#Title {{ font-size: 13px; font-weight: 600; color: {TEXT}; padding: 2px 0; }}
QLabel#Section {{ font-size: 11px; font-weight: 600; color: {TEXT_DIM}; letter-spacing: 1px;
                  padding-top: 8px; text-transform: uppercase; }}
QLabel#Dim {{ color: {TEXT_DIM}; }}
QLabel#Brand {{ font-size: 15px; font-weight: 700; color: {TEXT}; }}
QLabel#BrandAccent {{ font-size: 15px; font-weight: 700; color: {ACCENT}; }}
QLabel#Badge {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 9px; padding: 2px 8px; color: {TEXT_DIM}; }}

QPushButton {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 10px; }}
QPushButton:hover {{ border-color: #4a515b; background: #30353c; }}
QPushButton:pressed {{ background: #262a30; }}
QPushButton:disabled {{ color: #5d636c; border-color: #2c3036; }}
QPushButton:checked {{ background: #1e3b39; border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton#Primary {{ background: {ACCENT}; color: #0d1716; border: none; font-weight: 600; }}
QPushButton#Primary:hover {{ background: #45e0d2; }}
QPushButton#Primary:disabled {{ background: #2b4a47; color: #6b8a87; }}
QPushButton#Danger {{ color: {ERROR}; }}
QPushButton#Mode {{ padding: 5px 4px; border-radius: 5px; font-size: 11px; }}
QToolButton {{ background: transparent; border: none; border-radius: 5px; padding: 4px; }}
QToolButton:hover {{ background: #30353c; }}
QToolButton:checked {{ background: #1e3b39; }}

QComboBox, QLineEdit, QListWidget {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 5px; padding: 4px 6px;
    selection-background-color: #1e3b39; selection-color: {TEXT}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {BORDER}; }}
QListWidget::item {{ padding: 5px 4px; border-radius: 4px; }}
QListWidget::item:selected {{ background: #1e3b39; color: {TEXT}; }}
QListWidget::item:hover {{ background: #2a2f35; }}

QSlider::groove:horizontal {{ height: 4px; background: #3a3f47; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT_DARK}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; background: {TEXT}; }}
QSlider::handle:horizontal:hover {{ background: {ACCENT}; }}

QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 3px; border: 1px solid #4a515b; background: {BG}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QProgressBar {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 4px; height: 8px; text-align: center; font-size: 10px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; }}
QScrollBar::handle:vertical {{ background: #3a3f47; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QToolTip {{ background: {PANEL_2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
QMessageBox, QInputDialog, QFileDialog {{ background: {PANEL}; }}
QTabBar::tab {{ background: transparent; padding: 5px 12px; color: {TEXT_DIM}; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom-color: {ACCENT}; }}
"""
