"""应用启动入口: 图形界面 / 独立版图查看器 / 日志与环境初始化。

命令行启动::

    python -m dfm gui
    python -m dfm analyze board.brd --view

打包后的 exe 亦调用 :func:`main`。
"""
from __future__ import annotations

import logging
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
    except Exception:
        pass


def create_app(theme: Optional[str] = None):
    """创建 QApplication 并套用主题/图标/字体。"""
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
    """独立的版图交叉定位窗口 (CLI --view 用); 实现见 dfm.ui.viewer。"""
    from dfm.ui.viewer import run_viewer as _run

    return _run(analysis)


def main() -> int:
    """统一入口: 有子命令走 CLI, 否则启动界面。"""
    from dfm.cli import main as cli_main

    args = sys.argv[1:]
    if not args:
        return run_gui()
    return cli_main(args)


if __name__ == "__main__":
    sys.exit(main())
