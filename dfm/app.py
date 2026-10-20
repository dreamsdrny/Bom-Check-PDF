"""应用启动入口: 图形界面 / 独立版图查看器 / 日志与环境初始化。

命令行启动::

    python -m dfm gui
    python -m dfm analyze board.brd --view

打包后的 exe 亦调用 :func:`main`。
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any, Dict, Optional

from dfm import paths
from dfm.branding import APP_ID, APP_NAME


def setup_logging(level: int = logging.INFO) -> str:
    """初始化日志 (同时输出到文件与 stderr), 返回日志文件路径。"""
    path = paths.log_path()
    root = logging.getLogger()
    if root.handlers:
        return path
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    try:
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh.setLevel(logging.WARNING)
    root.addHandler(sh)
    return path


def set_windows_app_id() -> None:
    """设置 AppUserModelID, 让 Windows 任务栏显示正确图标并归组。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:  # noqa: BLE001
        pass


def create_app(theme: Optional[str] = None):
    """创建 QApplication 并套用主题/图标/字体。"""
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from dfm.ui.icons import app_icon
    from dfm.ui.theme import apply_theme

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName("Evan")
    app.setWindowIcon(app_icon())
    apply_theme(app, theme or "dark")
    return app


def run_gui(page: Optional[str] = None) -> int:
    """启动主窗口。"""
    setup_logging()
    set_windows_app_id()

    from dfm.ui.main_window import MainWindow
    from dfm.ui.state import AppState

    state = AppState()
    app = create_app(str(state.setting("theme", "dark")))
    win = MainWindow(state)
    win.show()
    if page:
        win.navigate(page)
    return app.exec()


def run_viewer(analysis: Dict[str, Any], page: Optional[str] = None) -> int:
    """独立的版图交叉定位窗口 (CLI --view 用)。"""
    setup_logging()
    set_windows_app_id()

    from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                                   QMainWindow, QSplitter, QVBoxLayout, QWidget)
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor

    from dfm.ui.theme import SEVERITY_COLORS
    from dfm.ui.widgets.board_view import BoardView
    from dfm.ui.widgets.common import button

    app = create_app("dark")
    win = QMainWindow()
    win.setWindowTitle(f"{APP_NAME} — 版图交叉定位")
    win.resize(1280, 820)

    central = QWidget()
    lay = QVBoxLayout(central)
    lay.setContentsMargins(10, 10, 10, 10)
    meta = analysis.get("board_meta") or {}
    risk = analysis.get("risk") or {}
    head = QLabel(f"{meta.get('board_name', '')}   评分 {risk.get('score', '-')}/100 "
                  f"({risk.get('risk_level', '-')})   问题 "
                  f"{(analysis.get('issue_summary') or {}).get('总计', 0)} 项")
    lay.addWidget(head)

    split = QSplitter(Qt.Horizontal)
    lay.addWidget(split, 1)
    issues = analysis.get("issues") or []
    lst = QListWidget()
    for i, it in enumerate(issues):
        if it.get("x") is None:
            continue
        item = QListWidgetItem(f"[{it.get('severity')}] {it.get('code')} "
                               f"{it.get('rule')} @({it['x']:.2f},{it['y']:.2f})")
        item.setData(Qt.UserRole, i)
        item.setForeground(QColor(SEVERITY_COLORS.get(it.get("severity"), "#8b8d98")))
        lst.addItem(item)
    view = BoardView()
    view.set_board(analysis.get("fab_data") or [], issues)
    split.addWidget(lst)
    split.addWidget(view)
    split.setSizes([340, 900])

    def on_item(item):
        idx = item.data(Qt.UserRole)
        if idx is not None:
            view.select_issue(int(idx))

    lst.itemClicked.connect(on_item)
    view.issue_clicked.connect(lambda idx: [
        lst.setCurrentItem(lst.item(r)) for r in range(lst.count())
        if lst.item(r).data(Qt.UserRole) == idx])

    bar = QHBoxLayout()
    bar.addWidget(button("适应窗口", "fit", "", view.fit))
    bar.addStretch(1)
    lay.addLayout(bar)
    win.setCentralWidget(central)
    win.show()
    return app.exec()


def main() -> int:
    """统一入口: 有子命令走 CLI, 否则启动界面。"""
    from dfm.cli import main as cli_main

    args = sys.argv[1:]
    if not args:
        return run_gui()
    return cli_main(args)


if __name__ == "__main__":
    sys.exit(main())
