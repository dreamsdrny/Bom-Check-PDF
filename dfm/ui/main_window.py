"""主窗口: 侧边导航 + 页面栈 + 顶栏快捷配置 + 状态栏。

页面通过 :class:`dfm.ui.pages.base.BasePage` 的子类自动装配, 新增页面只需:
  1. 在 dfm/ui/pages/ 下新建 BasePage 子类
  2. 加入本模块的 :data:`PAGES` 列表
即可出现在侧边导航中, 并自动接收分析结果/规则变化等事件。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow,
                               QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout,
                               QWidget)

from dfm import __version__
from dfm.branding import win_title
from dfm.core.capability import factory_labels, list_factories, list_levels
from dfm.ui.icons import app_icon, icon
from dfm.ui.pages.analysis import AnalysisPage
from dfm.ui.pages.base import BasePage
from dfm.ui.pages.board import BoardPage
from dfm.ui.pages.home import HomePage
from dfm.ui.pages.project import ProjectPage
from dfm.ui.pages.report import ReportPage
from dfm.ui.pages.results import ResultsPage
from dfm.ui.pages.rules import RulesPage
from dfm.ui.pages.settings import SettingsPage
from dfm.ui.state import AppState
from dfm.ui.theme import colors
from dfm.ui.widgets.common import BrandHeader, StatusBadge, button

#: 侧边导航页面 (按 group + order 排序)
PAGES: List[type] = [HomePage, ProjectPage, AnalysisPage, ResultsPage, BoardPage,
                     RulesPage, ReportPage, SettingsPage]

NAV_WIDTH = 216


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(self, state: Optional[AppState] = None):
        super().__init__()
        self.state = state or AppState()
        self.setWindowTitle(win_title())
        self.setWindowIcon(app_icon())
        self.pages: Dict[str, BasePage] = {}
        self.nav_buttons: Dict[str, QToolButton] = {}
        self._build()
        self._connect()
        self._restore_geometry()
        self.navigate(str(self.state.setting("last_page") or "home"))
        self._refresh_top()

    # ---------------- 构建 ----------------
    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)
        right_lay.addWidget(self._build_topbar())
        self.stack = QStackedWidget()
        right_lay.addWidget(self.stack, 1)
        root.addWidget(right, 1)

        self.status = self.statusBar()
        self.status_label = QLabel("就绪")
        self.status.addWidget(self.status_label, 1)
        self.coord_label = QLabel("")
        self.status.addPermanentWidget(self.coord_label)
        self.version_label = QLabel(f"v{__version__}")
        self.status.addPermanentWidget(self.version_label)

    def _build_sidebar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("Sidebar")
        bar.setFixedWidth(NAV_WIDTH)
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(BrandHeader())

        groups: Dict[str, List[type]] = {}
        for page in sorted(PAGES, key=lambda p: (p.order, p.page_id)):
            groups.setdefault(page.group, []).append(page)
        for group, items in groups.items():
            label = QLabel(group.upper())
            label.setObjectName("NavSection")
            lay.addWidget(label)
            for page in items:
                btn = QToolButton()
                btn.setObjectName("NavItem")
                btn.setText(f"  {page.title}")
                btn.setIcon(icon(page.icon, colors()["fg_dim"], 17))
                btn.setIconSize(QSize(17, 17))
                btn.setCheckable(True)
                btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
                btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                btn.setMinimumHeight(38)
                btn.setCursor(Qt.PointingHandCursor)
                btn.setToolTip(page.subtitle)
                btn.clicked.connect(lambda _=False, pid=page.page_id: self.navigate(pid))
                lay.addWidget(btn)
                self.nav_buttons[page.page_id] = btn

        lay.addStretch(1)
        foot = QHBoxLayout()
        foot.setContentsMargins(12, 8, 12, 12)
        foot.setSpacing(6)
        self.theme_btn = QToolButton()
        self.theme_btn.setObjectName("NavItem")
        self.theme_btn.setText("  切换主题")
        self.theme_btn.setIcon(icon("palette", colors()["fg_dim"], 16))
        self.theme_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.clicked.connect(self._toggle_theme)
        foot.addWidget(self.theme_btn, 1)
        lay.addLayout(foot)
        return bar

    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(64)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 10, 20, 10)
        lay.setSpacing(12)

        box = QVBoxLayout()
        box.setSpacing(1)
        self.page_title = QLabel("")
        self.page_title.setObjectName("TopTitle")
        self.page_sub = QLabel("")
        self.page_sub.setObjectName("TopSub")
        box.addWidget(self.page_title)
        box.addWidget(self.page_sub)
        lay.addLayout(box)
        lay.addStretch(1)

        self.src_chip = QLabel("")
        self.src_chip.setObjectName("BadgeIdle")
        lay.addWidget(self.src_chip)

        lay.addWidget(QLabel("板厂"))
        self.factory_box = QComboBox()
        self.factory_box.setMinimumWidth(120)
        self.factory_box.currentIndexChanged.connect(self._on_factory)
        lay.addWidget(self.factory_box)

        lay.addWidget(QLabel("等级"))
        self.level_box = QComboBox()
        self.level_box.setMinimumWidth(110)
        self.level_box.currentIndexChanged.connect(self._on_level)
        lay.addWidget(self.level_box)

        self.badge = StatusBadge()
        lay.addWidget(self.badge)

        self.run_btn = button("执行分析", "run", "Primary", self.run_analysis)
        self.run_btn.setMinimumHeight(34)
        lay.addWidget(self.run_btn)
        return bar

    def _connect(self) -> None:
        self.state.analysis_started.connect(self._on_started)
        self.state.analysis_progress.connect(self.set_status)
        self.state.analysis_finished.connect(self._on_finished)
        self.state.analysis_failed.connect(self._on_failed)
        self.state.config_changed.connect(self._refresh_top)
        self.state.rules_changed.connect(self._on_rules_changed)
        self.state.issue_selected.connect(self._on_issue_selected)

        run = QAction("执行分析", self)
        run.setShortcut(QKeySequence("F5"))
        run.triggered.connect(self.run_analysis)
        self.addAction(run)
        export = QAction("导出报告", self)
        export.setShortcut(QKeySequence("Ctrl+E"))
        export.triggered.connect(lambda: self.navigate("report"))
        self.addAction(export)

    # ---------------- 页面 ----------------
    def _ensure_pages(self) -> None:
        if self.pages:
            return
        for cls in PAGES:
            page = cls(self.state, self)
            self.pages[page.page_id] = page
            self.stack.addWidget(page)

    def navigate(self, page_id: str) -> None:
        self._ensure_pages()
        page = self.pages.get(page_id)
        if page is None:
            page = self.pages.get("home")
            page_id = "home"
        self.stack.setCurrentWidget(page)
        self.page_title.setText(page.title)
        self.page_sub.setText(page.subtitle)
        for pid, btn in self.nav_buttons.items():
            btn.setChecked(pid == page_id)
        icon_color = colors()["accent"]
        for pid, btn in self.nav_buttons.items():
            cls = next(c for c in PAGES if c.page_id == pid)
            btn.setIcon(icon(cls.icon, icon_color if pid == page_id else colors()["fg_dim"], 17))
        self.state.set_setting("last_page", page_id)
        page.on_show()

    def page(self, page_id: str) -> Optional[BasePage]:
        self._ensure_pages()
        return self.pages.get(page_id)

    # ---------------- 顶栏 ----------------
    def _refresh_top(self) -> None:
        cfg = self.state.config
        labels = factory_labels()
        self.factory_box.blockSignals(True)
        current = self.factory_box.currentData()
        self.factory_box.clear()
        for fid in list_factories():
            self.factory_box.addItem(labels.get(fid, fid), fid)
        i = self.factory_box.findData(cfg.get("factory") or current or "jlcpcb")
        self.factory_box.setCurrentIndex(max(0, i))
        self.factory_box.blockSignals(False)

        self._refresh_levels()
        src = cfg.get("source_path") or cfg.get("fab_path") or ""
        self.src_chip.setText(("数据源: " + os.path.basename(str(src)))
                              if src else "未选择数据源")

    def _refresh_levels(self) -> None:
        cfg = self.state.config
        fid = self.factory_box.currentData() or cfg.get("factory") or "jlcpcb"
        bt = cfg.get("board_type") or "rigid"
        levels = list_levels(bt, factory=fid) or ["standard"]
        self.level_box.blockSignals(True)
        self.level_box.clear()
        for lv in levels:
            self.level_box.addItem(lv, lv)
        i = self.level_box.findData(cfg.get("capability_level") or "standard")
        self.level_box.setCurrentIndex(max(0, i))
        self.level_box.blockSignals(False)

    def _on_factory(self, *_args) -> None:
        fid = self.factory_box.currentData()
        if fid:
            self.state.set_config(factory=fid)
            self._refresh_levels()

    def _on_level(self, *_args) -> None:
        lv = self.level_box.currentData()
        if lv:
            self.state.set_config(capability_level=lv)

    def _toggle_theme(self) -> None:
        cur = str(self.state.setting("theme", "dark"))
        self.apply_theme("light" if cur.startswith("dark") else "dark")

    def apply_theme(self, theme: str) -> None:
        from PySide6.QtWidgets import QApplication

        from dfm.ui.theme import apply_theme as _apply

        app = QApplication.instance()
        if app is not None:
            _apply(app, theme)
        self.state.set_setting("theme", theme)
        # 主题切换后重建图标颜色与页面样式
        from dfm.ui import icons

        icons.clear_cache()
        self._refresh_nav_icons()
        self.set_status(f"主题: {'深色' if theme == 'dark' else '浅色'}")

    def _refresh_nav_icons(self) -> None:
        current = self.stack.currentWidget()
        for pid, btn in self.nav_buttons.items():
            cls = next(c for c in PAGES if c.page_id == pid)
            active = self.pages.get(pid) is current
            btn.setIcon(icon(cls.icon, colors()["accent"] if active else colors()["fg_dim"], 17))

    # ---------------- 分析 ----------------
    def run_analysis(self) -> None:
        self._ensure_pages()
        page: AnalysisPage = self.pages["analysis"]
        self.navigate("analysis")
        page.start_analysis()

    def _on_started(self) -> None:
        self.badge.set_state("running")
        self.run_btn.setEnabled(False)
        for page in self.pages.values():
            page.on_analysis_started()

    def _on_finished(self, analysis: Dict[str, Any]) -> None:
        self.badge.set_state("done" if analysis.get("passed", True) else "error")
        self.run_btn.setEnabled(True)
        summary = analysis.get("issue_summary") or {}
        risk = analysis.get("risk") or {}
        self.set_status(
            f"分析完成: 问题 {summary.get('总计', 0)} 项 (危险 {summary.get('危险', 0)}) · "
            f"评分 {risk.get('score')}/100 ({risk.get('risk_level')})")
        for page in self.pages.values():
            try:
                page.on_analysis(analysis)
            except Exception:
                import traceback

                traceback.print_exc()

    def _on_failed(self, msg: str) -> None:
        self.badge.set_state("error")
        self.run_btn.setEnabled(True)
        self.set_status("分析失败: " + msg.split("\n")[0])

    def _on_rules_changed(self) -> None:
        for page in self.pages.values():
            try:
                page.on_rules_changed()
            except Exception:
                pass
        self._refresh_top()

    def _on_issue_selected(self, index: int) -> None:
        for pid in ("results", "board"):
            page = self.pages.get(pid)
            if page is not None and hasattr(page, "select_issue"):
                try:
                    page.select_issue(index)
                except Exception:
                    pass

    # ---------------- 其它 ----------------
    def set_status(self, text: str) -> None:
        self.status_label.setText(str(text))

    def _restore_geometry(self) -> None:
        win = self.state.setting("window") or {}
        w = int(win.get("width") or 1440)
        h = int(win.get("height") or 900)
        self.resize(w, h)
        self.setMinimumSize(1120, 700)
        if win.get("maximized"):
            self.showMaximized()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.state.set_setting("window", {
            "width": self.width(), "height": self.height(),
            "maximized": self.isMaximized(),
        })
        self.state.save_settings()
        super().closeEvent(event)
