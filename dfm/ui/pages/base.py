"""页面基类: 统一标题/图标/生命周期, 便于新增页面 (二次开发)。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtWidgets import QVBoxLayout, QWidget


class BasePage(QWidget):
    """所有功能页的基类。

    子类需设置类属性 ``page_id`` / ``title`` / ``subtitle`` / ``icon``,
    并按需重写 :meth:`on_show` / :meth:`on_analysis` / :meth:`on_rules_changed`。
    """

    page_id = "base"
    title = "页面"
    subtitle = ""
    icon = "grid"
    group = "工作台"
    order = 100

    def __init__(self, state, window=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.state = state
        self.window = window
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(20, 16, 20, 16)
        self.root.setSpacing(14)
        self.build()

    # ---- 子类实现 ----
    def build(self) -> None:
        """构建界面 (子类必须实现)。"""

    def on_show(self) -> None:
        """页面被切换到前台时调用。"""

    def on_analysis(self, analysis: Dict[str, Any]) -> None:
        """分析完成时调用。"""

    def on_analysis_started(self) -> None:
        """分析开始时调用。"""

    def on_rules_changed(self) -> None:
        """规则集变化时调用。"""

    def on_config_changed(self) -> None:
        """分析配置变化时调用。"""

    # ---- 工具 ----
    def set_status(self, text: str) -> None:
        if self.window is not None:
            self.window.set_status(text)

    def navigate(self, page_id: str) -> None:
        if self.window is not None:
            self.window.navigate(page_id)

    def run_analysis(self) -> None:
        if self.window is not None:
            self.window.run_analysis()
