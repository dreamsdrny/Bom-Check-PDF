"""问题清单表格 (模型/视图 + 过滤条 + 严重度着色 + 交叉定位信号)。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QToolButton, QVBoxLayout, QWidget)

from dfm.core.issue import SEVERITY_LEVELS
from dfm.ui.icons import icon
from dfm.ui.theme import MONO, SEVERITY_COLORS, colors

#: (键, 表头, 宽度, 对齐)
COLUMNS = [
    ("severity", "严重度", 74, Qt.AlignCenter),
    ("code", "代码", 86, Qt.AlignLeft),
    ("module", "检查", 104, Qt.AlignLeft),
    ("category", "类别", 74, Qt.AlignLeft),
    ("rule", "规则", 150, Qt.AlignLeft),
    ("count", "数量", 58, Qt.AlignCenter),
    ("measured", "实测", 76, Qt.AlignRight),
    ("required", "要求", 76, Qt.AlignRight),
    ("unit", "单位", 52, Qt.AlignCenter),
    ("layer", "层", 76, Qt.AlignLeft),
    ("net", "网络", 100, Qt.AlignLeft),
    ("x", "X (mm)", 78, Qt.AlignRight),
    ("y", "Y (mm)", 78, Qt.AlignRight),
    ("description", "问题描述", 460, Qt.AlignLeft),
    ("suggestion", "专家建议", 360, Qt.AlignLeft),
]

_SEV_ORDER = {s: i for i, s in enumerate(SEVERITY_LEVELS)}


def _fmt_num(v: Any) -> str:
    if v is None or isinstance(v, bool):
        return "—" if v is None else str(v)
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


class IssueModel(QAbstractTableModel):
    """问题清单模型; 内部维护过滤后的原始索引, 便于与版图交叉定位。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._issues: List[dict] = []
        self._view: List[int] = []
        self._severities: set = set()
        self._modules: set = set()
        self._text: str = ""
        self._selected: int = -1
        self._mono = QFont("Cascadia Mono")
        self._mono.setStyleHint(QFont.Monospace)
        self._mono.setPointSize(9)

    # ---- 数据 ----
    def set_issues(self, issues: List[dict]) -> None:
        self.beginResetModel()
        self._issues = list(issues or [])
        self._severities = set()
        self._modules = set()
        self.endResetModel()
        self.apply_filter()

    def issues(self) -> List[dict]:
        return list(self._issues)

    def original_index(self, row: int) -> int:
        if 0 <= row < len(self._view):
            return self._view[row]
        return -1

    def view_row(self, original: int) -> int:
        try:
            return self._view.index(original)
        except ValueError:
            return -1

    # ---- 过滤 ----
    def set_filter(self, severities: Optional[set] = None, modules: Optional[set] = None,
                   text: str = "") -> None:
        if severities is not None:
            self._severities = set(severities)
        if modules is not None:
            self._modules = set(modules)
        self._text = (text or "").strip().lower()
        self.apply_filter()

    def apply_filter(self) -> None:
        self.beginResetModel()
        view = []
        for i, it in enumerate(self._issues):
            if self._severities and it.get("severity") not in self._severities:
                continue
            if self._modules and str(it.get("module") or "") not in self._modules:
                continue
            if self._text:
                blob = " ".join(str(it.get(k) or "") for k in
                                ("code", "rule", "category", "description", "net",
                                 "layer", "module", "suggestion")).lower()
                if self._text not in blob:
                    continue
            view.append(i)
        view.sort(key=lambda i: (_SEV_ORDER.get(self._issues[i].get("severity", "中"), 9),
                                 str(self._issues[i].get("code", ""))))
        self._view = view
        self.endResetModel()

    def all_modules(self) -> List[str]:
        return sorted({str(it.get("module") or "") for it in self._issues
                       if it.get("module")})

    def severity_counts(self) -> Dict[str, int]:
        out = {s: 0 for s in SEVERITY_LEVELS}
        for it in self._issues:
            out[it.get("severity", "中")] = out.get(it.get("severity", "中"), 0) + 1
        return out

    def set_selected(self, original: int) -> None:
        self._selected = original
        if self._view:
            top = self.index(0, 0)
            bottom = self.index(len(self._view) - 1, len(COLUMNS) - 1)
            self.dataChanged.emit(top, bottom)

    # ---- QAbstractTableModel ----
    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._view)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section][1]
        if orientation == Qt.Vertical and role == Qt.DisplayRole:
            return str(section + 1)
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        oi = self._view[index.row()]
        it = self._issues[oi]
        key, _title, _w, align = COLUMNS[index.column()]
        if role == Qt.TextAlignmentRole:
            return int(align | Qt.AlignVCenter)
        if role == Qt.ForegroundRole:
            if key == "severity":
                return QBrush(QColor(SEVERITY_COLORS.get(it.get("severity"), "#8b8d98")))
            if key == "suggestion":
                return QBrush(QColor(colors()["fg_mut"]))
            return QBrush(QColor(colors()["fg"]))
        if role == Qt.FontRole and key in ("code", "measured", "required", "x", "y"):
            return self._mono
        if role == Qt.BackgroundRole:
            if oi == self._selected:
                return QBrush(QColor(colors()["sel"]))
            return None
        if role == Qt.ToolTipRole:
            return (f"[{it.get('severity')}] {it.get('code')} {it.get('rule')}\n"
                    f"{it.get('description')}\n建议: {it.get('suggestion')}\n"
                    f"检查: {it.get('check_name') or it.get('module')}"
                    + (f"\n定位点: {len(it.get('locations') or [])} 处"
                       if it.get("locations") else ""))
        if role != Qt.DisplayRole:
            return None
        if key == "count":
            return str(it.get("count", 1))
        if key in ("measured", "required"):
            return _fmt_num(it.get(key))
        if key in ("x", "y"):
            v = it.get(key)
            return f"{v:.3f}" if isinstance(v, (int, float)) else "—"
        return str(it.get(key) or "")


class SeverityChip(QToolButton):
    """严重度过滤开关 (点击切换是否显示该级别)。"""

    toggled_severity = Signal(str, bool)

    def __init__(self, severity: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.severity = severity
        self.setCheckable(True)
        self.setChecked(True)
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.setCursor(Qt.PointingHandCursor)
        self._color = SEVERITY_COLORS.get(severity, "#8b8d98")
        self.toggled.connect(lambda on: self.toggled_severity.emit(self.severity, on))
        self._refresh()

    def set_count(self, n: int) -> None:
        self._count = n
        self._refresh()

    def _refresh(self) -> None:
        n = getattr(self, "_count", None)
        self.setText(f"{self.severity}" + (f" {n}" if n is not None else ""))
        if self.isChecked():
            self.setStyleSheet(
                f"QToolButton{{background:{self._color};color:#0b1220;border:none;"
                f"border-radius:11px;padding:3px 12px;font-weight:600;font-size:12px;}}")
        else:
            self.setStyleSheet(
                f"QToolButton{{background:transparent;color:{colors()['fg_mut']};"
                f"border:1px solid {colors()['line']};border-radius:11px;"
                f"padding:3px 12px;font-size:12px;}}")


class IssueFilterBar(QWidget):
    """过滤条: 严重度开关 + 检查筛选 + 关键字搜索 + 计数。"""

    filter_changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.chips: Dict[str, SeverityChip] = {}
        for sev in ("危险", "高", "中", "低"):
            chip = SeverityChip(sev)
            chip.toggled_severity.connect(lambda _s, _on: self.filter_changed.emit())
            self.chips[sev] = chip
            lay.addWidget(chip)
        lay.addSpacing(8)
        self.module_box = QComboBox()
        self.module_box.setMinimumWidth(150)
        self.module_box.currentIndexChanged.connect(lambda _: self.filter_changed.emit())
        lay.addWidget(self.module_box)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索规则 / 描述 / 网络 / 层 ...")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(240)
        self.search.textChanged.connect(lambda _: self.filter_changed.emit())
        lay.addWidget(self.search, 1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("Muted")
        lay.addWidget(self.count_label)
        self.reset_btn = QToolButton()
        self.reset_btn.setIcon(icon("refresh", colors()["fg_dim"], 15))
        self.reset_btn.setToolTip("重置过滤")
        self.reset_btn.clicked.connect(self.reset)
        lay.addWidget(self.reset_btn)

    def set_counts(self, counts: Dict[str, int]) -> None:
        for sev, chip in self.chips.items():
            chip.set_count(int(counts.get(sev) or 0))

    def set_modules(self, modules: List[str], current: str = "") -> None:
        self.module_box.blockSignals(True)
        self.module_box.clear()
        self.module_box.addItem("全部检查", "")
        for m in modules:
            self.module_box.addItem(m, m)
        if current:
            i = self.module_box.findData(current)
            if i >= 0:
                self.module_box.setCurrentIndex(i)
        self.module_box.blockSignals(False)

    def reset(self) -> None:
        for chip in self.chips.values():
            chip.setChecked(True)
        self.module_box.setCurrentIndex(0)
        self.search.clear()
        self.filter_changed.emit()

    def severities(self) -> set:
        off = {sev for sev, chip in self.chips.items() if not chip.isChecked()}
        return set() if not off else set(SEVERITY_LEVELS) - off

    def module(self) -> str:
        return str(self.module_box.currentData() or "")


class IssueTable(QWidget):
    """问题清单视图 (表格 + 过滤条)。"""

    issue_activated = Signal(int)     # 双击: 原始索引
    issue_selected = Signal(int)      # 单击: 原始索引

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.filter_bar = IssueFilterBar()
        lay.addWidget(self.filter_bar)

        from PySide6.QtWidgets import QTableView

        self.view = QTableView()
        self.model = IssueModel(self)
        self.view.setModel(self.model)
        self.view.setSortingEnabled(False)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.view.setAlternatingRowColors(True)
        self.view.setShowGrid(False)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(30)
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.view.setWordWrap(False)
        header = self.view.horizontalHeader()
        header.setStretchLastSection(True)
        header.setHighlightSections(False)
        for i, (key, _t, width, _a) in enumerate(COLUMNS):
            self.view.setColumnWidth(i, width)
            header.setSectionResizeMode(i, QHeaderView.Interactive)
        header.setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.Stretch)
        lay.addWidget(self.view, 1)

        self.filter_bar.filter_changed.connect(self._apply_filter)
        self.view.clicked.connect(self._on_clicked)
        self.view.doubleClicked.connect(self._on_double)
        self.view.selectionModel().selectionChanged.connect(self._on_selection)

    # ---- 数据 ----
    def set_issues(self, issues: List[dict]) -> None:
        self.model.set_issues(issues)
        self.filter_bar.set_counts(self.model.severity_counts())
        self.filter_bar.set_modules(self.model.all_modules(),
                                    self.filter_bar.module())
        self._apply_filter()

    def clear(self) -> None:
        self.set_issues([])

    def _apply_filter(self) -> None:
        sev = self.filter_bar.severities()
        mod = self.filter_bar.module()
        self.model.set_filter(severities=sev or None,
                              modules={mod} if mod else None,
                              text=self.filter_bar.search.text())
        self.filter_bar.count_label.setText(
            f"显示 {self.model.rowCount()} / {len(self.model.issues())} 项")

    # ---- 交互 ----
    def _on_clicked(self, index: QModelIndex) -> None:
        oi = self.model.original_index(index.row())
        if oi >= 0:
            self.model.set_selected(oi)
            self.issue_selected.emit(oi)

    def _on_double(self, index: QModelIndex) -> None:
        oi = self.model.original_index(index.row())
        if oi >= 0:
            self.issue_activated.emit(oi)

    def _on_selection(self, *_args) -> None:
        idx = self.view.currentIndex()
        if idx.isValid():
            oi = self.model.original_index(idx.row())
            if oi >= 0:
                self.model.set_selected(oi)
                self.issue_selected.emit(oi)

    def select_issue(self, original: int, scroll: bool = True) -> None:
        """由外部 (版图点击/首页跳转) 选中某问题。"""
        row = self.model.view_row(original)
        self.model.set_selected(original)
        if row < 0:
            return
        index = self.model.index(row, 0)
        self.view.setCurrentIndex(index)
        if scroll:
            self.view.scrollTo(index, QAbstractItemView.PositionAtCenter)

    def current_issue(self) -> Optional[dict]:
        idx = self.view.currentIndex()
        if not idx.isValid():
            return None
        oi = self.model.original_index(idx.row())
        issues = self.model.issues()
        return issues[oi] if 0 <= oi < len(issues) else None
