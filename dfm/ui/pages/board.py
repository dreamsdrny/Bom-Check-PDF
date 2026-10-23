"""版图页: 图层控制 + 版图渲染 + 问题交叉定位。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QScrollArea, QSplitter, QVBoxLayout,
                               QWidget)

from dfm.report.svg import layer_color
from dfm.ui.pages.base import BasePage
from dfm.ui.theme import SEVERITY_COLORS, colors
from dfm.ui.widgets.board_view import BoardView
from dfm.ui.widgets.common import Card, EmptyHint, button, muted


class LayerRow(QWidget):
    """图层开关行: 色块 + 名称 + 复选框。"""

    def __init__(self, name: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.layer_name = name
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(7)
        swatch = QLabel()
        pm = QPixmap(12, 12)
        pm.fill(QColor(layer_color(name)))
        swatch.setPixmap(pm)
        swatch.setFixedSize(12, 12)
        lay.addWidget(swatch)
        self.box = QCheckBox(name)
        self.box.setChecked(True)
        self.box.setStyleSheet("font-size:12px;")
        lay.addWidget(self.box, 1)

    def is_checked(self) -> bool:
        return self.box.isChecked()

    def set_checked(self, on: bool) -> None:
        self.box.setChecked(on)


class BoardPage(BasePage):
    page_id = "board"
    title = "版图定位"
    subtitle = "图层显隐 / 缩放平移 / 问题双向交叉定位"
    icon = "layers"
    group = "分析"
    order = 50

    def build(self) -> None:
        self.view = BoardView()
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(6)
        self.root.addWidget(splitter, 1)

        # ---- 左: 图层 + 问题 ----
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(10)

        layer_card = Card("图层", "勾选控制显隐")
        lbar = QHBoxLayout()
        lbar.setSpacing(6)
        lbar.addWidget(button("全显", "eye", "Ghost", lambda: self._all_layers(True)))
        lbar.addWidget(button("全隐", "close", "Ghost", lambda: self._all_layers(False)))
        lbar.addWidget(button("仅铜层", "chip", "Ghost", self._copper_only))
        lbar.addStretch(1)
        layer_card.add_layout(lbar)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        self.layers_lay = QVBoxLayout(host)
        self.layers_lay.setContentsMargins(0, 0, 6, 0)
        self.layers_lay.setSpacing(2)
        self.layers_lay.addStretch(1)
        scroll.setWidget(host)
        layer_card.add_widget(scroll, 1)
        left_lay.addWidget(layer_card, 2)

        issue_card = Card("问题定位列表", "单击定位, 双击放大")
        self.issue_list = QListWidget()
        self.issue_list.setIconSize(QSize(10, 10))
        self.issue_list.itemClicked.connect(self._on_list_clicked)
        self.issue_list.itemDoubleClicked.connect(self._on_list_double)
        issue_card.add_widget(self.issue_list, 1)
        left_lay.addWidget(issue_card, 3)

        splitter.addWidget(left)

        # ---- 右: 版图 ----
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(button("放大", "zoom-in", "", lambda: self.view.zoom_by(1.25)))
        bar.addWidget(button("缩小", "zoom-out", "", lambda: self.view.zoom_by(0.8)))
        bar.addWidget(button("适应窗口", "fit", "", self.view.fit))
        bar.addSpacing(10)
        bar.addWidget(button("上一处", "chevron-right", "", self._prev))
        bar.addWidget(button("下一处", "chevron-right", "", self._next))
        bar.addStretch(1)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("Muted")
        bar.addWidget(self.zoom_label)
        self.coord_label = QLabel("X —   Y —")
        self.coord_label.setStyleSheet(f"font-family:Consolas,monospace;"
                                       f"color:{colors()['fg_dim']};")
        bar.addWidget(self.coord_label)
        right_lay.addWidget(self._wrap(bar))

        self.view.issue_clicked.connect(self._on_marker_clicked)
        self.view.cursor_moved.connect(self._on_cursor)
        self.view.zoom_changed.connect(
            lambda z: self.zoom_label.setText(f"{z * 100:.0f}%"))
        right_lay.addWidget(self.view, 1)

        self.hint = QLabel("滚轮缩放 · 左键拖动平移 · 双击适应窗口 · 点击标记定位问题")
        self.hint.setObjectName("Muted")
        right_lay.addWidget(self.hint)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 980])

        self._layer_rows: Dict[str, LayerRow] = {}
        self._current = -1
        self._empty = EmptyHint("尚无可显示的版图几何\n需要 BRD/JSON 或 Gerber/ODB++ 数据源",
                                "layers")

    def _wrap(self, layout) -> QWidget:
        host = QWidget()
        host.setLayout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        return host

    # ---------------- 数据 ----------------
    def on_analysis(self, analysis: Dict[str, Any]) -> None:
        fab = analysis.get("fab_data") or []
        issues = analysis.get("issues") or []
        if not fab:
            self._show_empty()
            return
        self.view.set_board(fab, issues)
        self._build_layers()
        self._build_issue_list(issues)
        meta = analysis.get("board_meta") or {}
        self.hint.setText(f"{meta.get('board_name', '')} · "
                          f"{len(fab)} 个数据层 · {len(issues)} 项问题   |   "
                          f"滚轮缩放 · 拖动平移 · 双击适应窗口")

    def _show_empty(self) -> None:
        self.view.set_board([], [])
        self.issue_list.clear()
        for row in self._layer_rows.values():
            row.deleteLater()
        self._layer_rows.clear()
        self.hint.setText("无几何数据: 仅参数化检查时无版图可显示")

    def _build_layers(self) -> None:
        for row in self._layer_rows.values():
            row.deleteLater()
        self._layer_rows.clear()
        names = self.view.layer_names()
        for i, name in enumerate(names):
            row = LayerRow(name)
            row.box.toggled.connect(lambda on, n=name: self.view.set_layer_visible(n, on))
            self._layer_rows[name] = row
            self.layers_lay.insertWidget(i, row)
        if not names:
            self.layers_lay.insertWidget(0, muted("(无图层)"))

    def _build_issue_list(self, issues: List[dict]) -> None:
        self.issue_list.clear()
        for i, it in enumerate(issues):
            if it.get("x") is None:
                continue
            label = (f"[{it.get('severity')}] {it.get('code')} {it.get('rule')} "
                     f"@({it['x']:.2f}, {it['y']:.2f})")
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, i)
            item.setForeground(QColor(SEVERITY_COLORS.get(it.get("severity"), "#8b8d98")))
            self.issue_list.addItem(item)
        if not self.issue_list.count():
            self.issue_list.addItem("(所有问题均无坐标定位)")

    def _all_layers(self, on: bool) -> None:
        for row in self._layer_rows.values():
            row.set_checked(on)
        self.view.set_all_layers_visible(on)

    def _copper_only(self) -> None:
        for name, row in self._layer_rows.items():
            low = name.lower()
            on = not any(k in low for k in ("mask", "silk", "legend", "paste", "gto", "gbo"))
            row.set_checked(on)
            self.view.set_layer_visible(name, on)

    # ---------------- 联动 ----------------
    def _on_list_clicked(self, item: QListWidgetItem) -> None:
        idx = item.data(Qt.UserRole)
        if idx is None:
            return
        self._current = int(idx)
        self.view.select_issue(self._current)
        self.state.issue_selected.emit(self._current)

    def _on_list_double(self, item: QListWidgetItem) -> None:
        idx = item.data(Qt.UserRole)
        if idx is not None:
            self._current = int(idx)
            self.view.select_issue(self._current)

    def _on_marker_clicked(self, index: int) -> None:
        self._current = index
        self.state.select_issue(index)
        for i in range(self.issue_list.count()):
            item = self.issue_list.item(i)
            if item.data(Qt.UserRole) == index:
                self.issue_list.setCurrentItem(item)
                self.issue_list.scrollToItem(item)
                break

    def _on_cursor(self, x: float, y: float) -> None:
        self.coord_label.setText(f"X {x:9.3f}   Y {y:9.3f} mm")

    def select_issue(self, index: int) -> None:
        self._current = index
        self.view.select_issue(index)
        for i in range(self.issue_list.count()):
            item = self.issue_list.item(i)
            if item.data(Qt.UserRole) == index:
                self.issue_list.setCurrentItem(item)
                break

    def _step(self, delta: int) -> None:
        count = self.issue_list.count()
        if not count:
            return
        cur = self.issue_list.currentRow()
        nxt = (cur + delta) % count
        item = self.issue_list.item(nxt)
        self.issue_list.setCurrentItem(item)
        self._on_list_clicked(item)

    def _prev(self) -> None:
        self._step(-1)

    def _next(self) -> None:
        self._step(1)

    def on_show(self) -> None:
        if self.state.analysis:
            if not self.view.has_board():
                self.on_analysis(self.state.analysis)
