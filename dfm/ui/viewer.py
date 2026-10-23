"""独立版图交叉定位窗口 — 不依赖主窗口, 供 CLI ``--view`` 与脚本调用。

用法::

    from dfm.ui.viewer import ViewerWindow, run_viewer
    run_viewer(analysis)                      # 阻塞显示 (创建 QApplication)
    win = ViewerWindow(analysis); win.show()  # 已有 QApplication 时嵌入使用
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QMainWindow, QSplitter, QVBoxLayout, QWidget)

from dfm.ui.theme import SEVERITY_COLORS
from dfm.ui.widgets.board_view import BoardView
from dfm.ui.widgets.common import button


class ViewerWindow(QMainWindow):
    """版图 + 问题列表的双向交叉定位窗口。"""

    def __init__(self, analysis: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        from dfm.branding import win_title

        self.analysis = analysis or {}
        self.setWindowTitle(win_title("版图交叉定位"))
        self.resize(1280, 820)

        meta = self.analysis.get("board_meta") or {}
        risk = self.analysis.get("risk") or {}
        summary = self.analysis.get("issue_summary") or {}
        self.issues: List[dict] = self.analysis.get("issues") or []

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        head = QLabel(
            f"{meta.get('board_name', '')}   评分 {risk.get('score', '-')}/100 "
            f"({risk.get('risk_level', '-')})   问题 {summary.get('总计', 0)} 项   "
            f"{meta.get('rule_set', '')}")
        lay.addWidget(head)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.view = BoardView()
        bar.addWidget(button("放大", "zoom-in", "", lambda: self.view.zoom_by(1.25)))
        bar.addWidget(button("缩小", "zoom-out", "", lambda: self.view.zoom_by(0.8)))
        bar.addWidget(button("适应窗口", "fit", "", self.view.fit))
        bar.addWidget(button("上一处", "chevron-right", "", lambda: self._step(-1)))
        bar.addWidget(button("下一处", "chevron-right", "", lambda: self._step(1)))
        bar.addStretch(1)
        self.zoom_label = QLabel("100%")
        self.coord_label = QLabel("X —   Y —")
        bar.addWidget(self.zoom_label)
        bar.addWidget(self.coord_label)
        lay.addWidget(self._wrap(bar))

        split = QSplitter(Qt.Horizontal)
        lay.addWidget(split, 1)

        self.list = QListWidget()
        for i, it in enumerate(self.issues):
            if it.get("x") is None:
                continue
            item = QListWidgetItem(f"[{it.get('severity')}] {it.get('code')} "
                                   f"{it.get('rule')} @({it['x']:.2f},{it['y']:.2f})")
            item.setData(Qt.UserRole, i)
            item.setForeground(QColor(SEVERITY_COLORS.get(it.get("severity"), "#8b8d98")))
            self.list.addItem(item)
        if not self.list.count():
            self.list.addItem("(无可定位的问题; 需要 BRD/JSON 或 Gerber/ODB++ 数据源)")

        self.view.set_board(self.analysis.get("fab_data") or [], self.issues)
        self.view.zoom_changed.connect(lambda z: self.zoom_label.setText(f"{z * 100:.0f}%"))
        self.view.cursor_moved.connect(
            lambda x, y: self.coord_label.setText(f"X {x:9.3f}   Y {y:9.3f} mm"))
        self.view.issue_clicked.connect(self._on_marker)
        self.list.itemClicked.connect(self._on_item)
        self.list.itemDoubleClicked.connect(self._on_item)

        split.addWidget(self.list)
        split.addWidget(self.view)
        split.setSizes([340, 900])
        self.setCentralWidget(central)

    def _wrap(self, layout) -> QWidget:
        host = QWidget()
        host.setLayout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        return host

    def _on_item(self, item: QListWidgetItem) -> None:
        idx = item.data(Qt.UserRole)
        if idx is not None:
            self.view.select_issue(int(idx))

    def _on_marker(self, index: int) -> None:
        for r in range(self.list.count()):
            item = self.list.item(r)
            if item.data(Qt.UserRole) == index:
                self.list.setCurrentItem(item)
                self.list.scrollToItem(item)
                break

    def _step(self, delta: int) -> None:
        if not self.list.count():
            return
        nxt = (self.list.currentRow() + delta) % self.list.count()
        item = self.list.item(nxt)
        self.list.setCurrentItem(item)
        self._on_item(item)


def run_viewer(analysis: Dict[str, Any]) -> int:
    """创建 QApplication 并阻塞显示独立版图窗口 (CLI ``--view`` 用)。"""
    from dfm.app import create_app, set_windows_app_id, setup_logging

    setup_logging()
    set_windows_app_id()
    app = create_app("dark")
    win = ViewerWindow(analysis)
    win.show()
    return app.exec()
