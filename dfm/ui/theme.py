"""界面主题 (深色/浅色) — 统一配色、字体与控件样式。

设计取向: 参考现代 EDA/IDE 的深色工作台 (低饱和背景 + 单一强调色 + 分级语义色),
所有样式集中在本模块, 页面代码只用语义对象名 (objectName) 而不写内联样式,
便于整体换肤与二次开发定制。

用法::

    from dfm.ui.theme import apply_theme
    apply_theme(app, "dark")
"""
from __future__ import annotations

from typing import Dict

DARK: Dict[str, str] = {
    "bg": "#0f1419",
    "bg_alt": "#131a21",
    "panel": "#161d25",
    "panel_alt": "#1b242e",
    "raise": "#212c37",
    "line": "#26313c",
    "line_soft": "#1e2731",
    "fg": "#e6edf3",
    "fg_dim": "#a7b4c2",
    "fg_mut": "#7b8896",
    "accent": "#4c8dff",
    "accent_dim": "#2f5fa8",
    "accent_soft": "#1b2c44",
    "ok": "#3fb950",
    "warn": "#d29922",
    "danger": "#f0554d",
    "info": "#58a6ff",
    "sel": "#1f3350",
    "hover": "#1d2733",
    "grid": "#1c242d",
    "sidebar": "#111820",
}

LIGHT: Dict[str, str] = {
    "bg": "#f5f7fa",
    "bg_alt": "#eef1f6",
    "panel": "#ffffff",
    "panel_alt": "#f7f9fc",
    "raise": "#eaeef4",
    "line": "#d6dde6",
    "line_soft": "#e5eaf1",
    "fg": "#1c2530",
    "fg_dim": "#48566a",
    "fg_mut": "#6f7d8f",
    "accent": "#2f6fd0",
    "accent_dim": "#9dbdf0",
    "accent_soft": "#e8f0fd",
    "ok": "#1f883d",
    "warn": "#9a6700",
    "danger": "#cf222e",
    "info": "#0969da",
    "sel": "#dce9fb",
    "hover": "#eef3fa",
    "grid": "#e8edf3",
    "sidebar": "#ffffff",
}

#: 严重度语义色 (与报告一致)
SEVERITY_COLORS = {
    "危险": "#e5484d",
    "高": "#f76808",
    "中": "#ffc53d",
    "低": "#46a758",
    "关闭": "#8b8d98",
}

FAMILY = '"Segoe UI","Microsoft YaHei","PingFang SC",system-ui,sans-serif'
MONO = '"Cascadia Mono",Consolas,"Courier New",monospace'

_QSS = """
*{{font-family:{family};}}
QWidget{{background:transparent;color:{fg};font-size:13px;}}
QMainWindow,QDialog{{background:{bg};}}
QToolTip{{background:{raise};color:{fg};border:1px solid {line};padding:5px 8px;
border-radius:6px;font-size:12px;}}

/* ---------- 侧边导航 ---------- */
QFrame#Sidebar{{background:{sidebar};border-right:1px solid {line};}}
QLabel#BrandTitle{{font-size:16px;font-weight:700;color:{fg};letter-spacing:.4px;}}
QLabel#BrandSub{{font-size:11px;color:{fg_mut};}}
QToolButton#NavItem{{background:transparent;border:none;border-radius:9px;
padding:10px 12px;text-align:left;color:{fg_dim};font-size:13px;}}
QToolButton#NavItem:hover{{background:{hover};color:{fg};}}
QToolButton#NavItem:checked{{background:{accent_soft};color:{accent};font-weight:600;}}
QToolButton#NavSection{{background:transparent;border:none;color:{fg_mut};
font-size:11px;padding:12px 12px 4px 12px;letter-spacing:.6px;}}

/* ---------- 顶栏 ---------- */
QFrame#TopBar{{background:{bg_alt};border-bottom:1px solid {line};}}
QLabel#TopTitle{{font-size:15px;font-weight:600;}}
QLabel#TopSub{{font-size:11px;color:{fg_mut};}}

/* ---------- 卡片 ---------- */
QFrame#Card{{background:{panel};border:1px solid {line};border-radius:12px;}}
QFrame#CardFlat{{background:{panel_alt};border:1px solid {line_soft};border-radius:10px;}}
QLabel#CardTitle{{font-size:13px;font-weight:600;color:{fg};}}
QLabel#CardHint{{font-size:11px;color:{fg_mut};}}
QLabel#SectionTitle{{font-size:12px;font-weight:600;color:{fg_dim};letter-spacing:.4px;}}

/* ---------- 按钮 ---------- */
QPushButton,QToolButton#Btn{{background:{raise};border:1px solid {line};border-radius:8px;
padding:7px 14px;color:{fg};font-size:13px;}}
QPushButton:hover,QToolButton#Btn:hover{{background:{hover};border-color:{accent_dim};}}
QPushButton:pressed{{background:{sel};}}
QPushButton:disabled{{color:{fg_mut};background:{panel_alt};border-color:{line_soft};}}
QPushButton#Primary{{background:{accent};border:1px solid {accent};color:#ffffff;font-weight:600;}}
QPushButton#Primary:hover{{background:{accent_dim};}}
QPushButton#Primary:disabled{{background:{panel_alt};border-color:{line};color:{fg_mut};}}
QPushButton#Danger{{background:transparent;border:1px solid {danger};color:{danger};}}
QPushButton#Ghost{{background:transparent;border:1px solid transparent;color:{fg_dim};}}
QPushButton#Ghost:hover{{background:{hover};color:{fg};}}

/* ---------- 输入 ---------- */
QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QPlainTextEdit,QTextEdit,QDateEdit{{
background:{bg_alt};border:1px solid {line};border-radius:8px;padding:6px 9px;
color:{fg};selection-background-color:{accent};selection-color:#fff;}}
QLineEdit:focus,QComboBox:focus,QSpinBox:focus,QDoubleSpinBox:focus,
QPlainTextEdit:focus,QTextEdit:focus{{border:1px solid {accent};}}
QLineEdit:disabled,QComboBox:disabled{{color:{fg_mut};background:{panel_alt};}}
QComboBox::drop-down{{border:none;width:20px;}}
QComboBox::down-arrow{{image:none;border-left:4px solid transparent;
border-right:4px solid transparent;border-top:5px solid {fg_dim};margin-right:8px;}}
QComboBox QAbstractItemView{{background:{panel};border:1px solid {line};
selection-background-color:{sel};selection-color:{fg};outline:none;padding:4px;}}
QCheckBox,QRadioButton{{spacing:7px;color:{fg_dim};background:transparent;}}
QCheckBox::indicator,QRadioButton::indicator{{width:16px;height:16px;
border:1px solid {line};background:{bg_alt};border-radius:4px;}}
QRadioButton::indicator{{border-radius:8px;}}
QCheckBox::indicator:hover,QRadioButton::indicator:hover{{border-color:{accent};}}
QCheckBox::indicator:checked,QRadioButton::indicator:checked{{background:{accent};
border-color:{accent};}}

/* ---------- 表格 / 树 / 列表 ---------- */
QTableView,QTreeView,QListView{{background:{panel};alternate-background-color:{panel_alt};
border:1px solid {line};border-radius:10px;gridline-color:{grid};outline:none;
selection-background-color:{sel};selection-color:{fg};}}
QHeaderView::section{{background:{panel_alt};color:{fg_dim};border:none;
border-right:1px solid {line_soft};border-bottom:1px solid {line};
padding:7px 9px;font-size:12px;font-weight:600;}}
QTableCornerButton::section{{background:{panel_alt};border:none;}}
QTableView::item,QTreeView::item,QListView::item{{padding:5px 8px;border:none;}}
QTableView::item:hover,QTreeView::item:hover,QListView::item:hover{{background:{hover};}}
QTableView::item:selected,QTreeView::item:selected,QListView::item:selected{{
background:{sel};color:{fg};}}

/* ---------- 选项卡 ---------- */
QTabWidget::pane{{border:1px solid {line};border-radius:10px;background:{panel};top:-1px;}}
QTabBar::tab{{background:transparent;color:{fg_mut};padding:8px 16px;border:none;
border-bottom:2px solid transparent;font-size:13px;}}
QTabBar::tab:hover{{color:{fg};}}
QTabBar::tab:selected{{color:{accent};border-bottom:2px solid {accent};font-weight:600;}}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical{{background:transparent;width:11px;margin:2px;}}
QScrollBar::handle:vertical{{background:{raise};border-radius:5px;min-height:28px;}}
QScrollBar::handle:vertical:hover{{background:{accent_dim};}}
QScrollBar:horizontal{{background:transparent;height:11px;margin:2px;}}
QScrollBar::handle:horizontal{{background:{raise};border-radius:5px;min-width:28px;}}
QScrollBar::handle:horizontal:hover{{background:{accent_dim};}}
QScrollBar::add-line,QScrollBar::sub-line{{width:0;height:0;}}
QScrollBar::add-page,QScrollBar::sub-page{{background:transparent;}}

/* ---------- 进度 / 状态 / 分隔 ---------- */
QProgressBar{{background:{bg_alt};border:1px solid {line};border-radius:7px;
height:14px;text-align:center;color:{fg_dim};font-size:11px;}}
QProgressBar::chunk{{background:{accent};border-radius:6px;}}
QProgressBar#Busy::chunk{{background:{accent};}}
QStatusBar{{background:{bg_alt};border-top:1px solid {line};color:{fg_mut};font-size:12px;}}
QStatusBar::item{{border:none;}}
QFrame#Divider{{background:{line};max-height:1px;border:none;}}
QSplitter::handle{{background:{line};}}
QSplitter::handle:horizontal{{width:1px;}}
QSplitter::handle:vertical{{height:1px;}}
QMenu{{background:{panel};border:1px solid {line};border-radius:9px;padding:5px;}}
QMenu::item{{padding:7px 20px;border-radius:6px;color:{fg_dim};}}
QMenu::item:selected{{background:{sel};color:{fg};}}
QMenu::separator{{height:1px;background:{line};margin:4px 6px;}}

/* ---------- 语义标签 ---------- */
QLabel#Muted{{color:{fg_mut};font-size:12px;}}
QLabel#Dim{{color:{fg_dim};}}
QLabel#Big{{font-size:26px;font-weight:700;}}
QLabel#ScoreOk{{font-size:34px;font-weight:700;color:{ok};}}
QLabel#ScoreWarn{{font-size:34px;font-weight:700;color:{warn};}}
QLabel#ScoreBad{{font-size:34px;font-weight:700;color:{danger};}}
QLabel#BadgeOk{{background:{ok};color:#0b1220;border-radius:10px;padding:2px 10px;font-weight:600;}}
QLabel#BadgeWarn{{background:{warn};color:#0b1220;border-radius:10px;padding:2px 10px;font-weight:600;}}
QLabel#BadgeBad{{background:{danger};color:#ffffff;border-radius:10px;padding:2px 10px;font-weight:600;}}
QLabel#BadgeIdle{{background:{raise};color:{fg_dim};border-radius:10px;padding:2px 10px;}}

/* ---------- 日志 ---------- */
QPlainTextEdit#Log{{background:#0b0f14;color:#c9d4e0;border:1px solid {line};
border-radius:10px;font-family:{mono};font-size:12px;padding:8px;}}
"""


def qss(theme: str = "dark") -> str:
    colors = LIGHT if str(theme).lower().startswith("light") else DARK
    return _QSS.format(family=FAMILY, mono=MONO, **colors)


def colors(theme: str = "dark") -> Dict[str, str]:
    return dict(LIGHT if str(theme).lower().startswith("light") else DARK)


def apply_theme(app, theme: str = "dark") -> None:
    """给 QApplication 套用主题 (含调色板与默认字体)。"""
    from PySide6.QtGui import QColor, QFont, QPalette

    c = colors(theme)
    app.setStyleSheet(qss(theme))
    font = QFont("Segoe UI", 9)
    font.setFamilies([f.strip('"') for f in FAMILY.split(",")])
    app.setFont(font)

    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(c["bg"]))
    pal.setColor(QPalette.WindowText, QColor(c["fg"]))
    pal.setColor(QPalette.Base, QColor(c["bg_alt"]))
    pal.setColor(QPalette.AlternateBase, QColor(c["panel_alt"]))
    pal.setColor(QPalette.Text, QColor(c["fg"]))
    pal.setColor(QPalette.Button, QColor(c["raise"]))
    pal.setColor(QPalette.ButtonText, QColor(c["fg"]))
    pal.setColor(QPalette.Highlight, QColor(c["accent"]))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ToolTipBase, QColor(c["raise"]))
    pal.setColor(QPalette.ToolTipText, QColor(c["fg"]))
    pal.setColor(QPalette.PlaceholderText, QColor(c["fg_mut"]))
    pal.setColor(QPalette.Link, QColor(c["accent"]))
    app.setPalette(pal)


def severity_color(severity: str) -> str:
    return SEVERITY_COLORS.get(severity, "#8b8d98")


def score_object_name(score: float) -> str:
    """评分标签的语义 objectName (ScoreOk / ScoreWarn / ScoreBad)"""
    if score is None:
        return "ScoreWarn"
    if score >= 85:
        return "ScoreOk"
    if score >= 60:
        return "ScoreWarn"
    return "ScoreBad"
