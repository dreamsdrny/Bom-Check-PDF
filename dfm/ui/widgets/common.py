"""通用界面构件: 卡片、路径选择行、统计卡、评分仪表、日志、徽标。

所有构件只依赖 :mod:`dfm.ui.theme` 与 :mod:`dfm.ui.icons`, 不含业务逻辑,
可直接在二次开发的新页面中复用。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QTextCharFormat
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from dfm.ui.icons import icon, logo_pixmap, pixmap
from dfm.ui.theme import SEVERITY_COLORS, colors


# ============================================================
# 容器
# ============================================================
class Card(QFrame):
    """带标题的卡片容器 (objectName=Card, 样式见 theme)。"""

    def __init__(self, title: str = "", hint: str = "", parent: Optional[QWidget] = None,
                 flat: bool = False):
        super().__init__(parent)
        self.setObjectName("CardFlat" if flat else "Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)
        self.header = QHBoxLayout()
        self.header.setSpacing(8)
        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("CardTitle")
        self.hint_label = QLabel(hint, self)
        self.hint_label.setObjectName("CardHint")
        self.header.addWidget(self.title_label)
        self.header.addWidget(self.hint_label, 1)
        self.hint_label.setVisible(bool(hint))
        outer.addLayout(self.header)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(8)
        outer.addLayout(self.body, 1)

    def set_title(self, title: str, hint: str = "") -> None:
        self.title_label.setText(title)
        if hint:
            self.hint_label.setText(hint)
            self.hint_label.setVisible(True)

    def add_widget(self, w: QWidget, stretch: int = 0) -> QWidget:
        self.body.addWidget(w, stretch)
        return w

    def add_layout(self, layout, stretch: int = 0):
        """加入子布局; 若误传 QWidget 则自动按控件处理。"""
        if isinstance(layout, QWidget):
            return self.add_widget(layout, stretch)
        self.body.addLayout(layout, stretch)
        return layout

    def add_header_widget(self, w: QWidget) -> QWidget:
        self.header.addWidget(w)
        return w


def divider(vertical: bool = False) -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFrameShape(QFrame.VLine if vertical else QFrame.HLine)
    if vertical:
        line.setFixedWidth(1)
    else:
        line.setFixedHeight(1)
    return line


def muted(text: str, object_name: str = "Muted") -> QLabel:
    lab = QLabel(text)
    lab.setObjectName(object_name)
    lab.setWordWrap(True)
    return lab


# ============================================================
# 表单行
# ============================================================
class PathRow(QWidget):
    """标签 + 路径输入 + 浏览按钮 (文件/目录)。"""

    path_changed = Signal(str)

    def __init__(self, label: str, mode: str = "file", hint: str = "",
                 filters: str = "", parent: Optional[QWidget] = None,
                 label_width: int = 96):
        super().__init__(parent)
        self.mode = mode
        self.filters = filters or "所有文件 (*.*)"
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.label = QLabel(label)
        self.label.setObjectName("Dim")
        self.label.setFixedWidth(label_width)
        lay.addWidget(self.label)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(hint)
        self.edit.setClearButtonEnabled(True)
        self.edit.textChanged.connect(lambda t: self.path_changed.emit(t))
        lay.addWidget(self.edit, 1)
        self.btn = QPushButton()
        self.btn.setIcon(icon("folder", colors()["fg_dim"], 16))
        self.btn.setText("浏览")
        self.btn.setFixedWidth(84)
        self.btn.clicked.connect(self._browse)
        lay.addWidget(self.btn)

    def _browse(self) -> None:
        start = self.edit.text().strip() or ""
        if self.mode == "dir":
            p = QFileDialog.getExistingDirectory(self, f"选择{self.label.text()}", start)
        else:
            p, _ = QFileDialog.getOpenFileName(self, f"选择{self.label.text()}", start,
                                               self.filters)
        if p:
            self.edit.setText(p.replace("\\", "/"))

    def text(self) -> str:
        return self.edit.text().strip()

    def set_text(self, value: str) -> None:
        self.edit.setText(value or "")



def combo(items: Sequence[Any], current: Any = None, width: int = 0) -> QComboBox:
    """items 可为 [(显示文本, 数据), ...] 或 [文本, ...]"""
    box = QComboBox()
    for it in items:
        if isinstance(it, (tuple, list)):
            box.addItem(str(it[0]), it[1])
        else:
            box.addItem(str(it), it)
    if current is not None:
        i = box.findData(current)
        box.setCurrentIndex(i if i >= 0 else 0)
    if width:
        box.setFixedWidth(width)
    return box


def button(text: str, icon_name: str = "", kind: str = "",
           handler: Optional[Callable] = None, tooltip: str = "") -> QPushButton:
    btn = QPushButton(text)
    if kind:
        btn.setObjectName(kind)
    if icon_name:
        col = "#ffffff" if kind == "Primary" else colors()["fg_dim"]
        btn.setIcon(icon(icon_name, col, 16))
    if tooltip:
        btn.setToolTip(tooltip)
    if handler:
        btn.clicked.connect(handler)
    return btn


# ============================================================
# 展示构件
# ============================================================
class StatCard(QFrame):
    """统计卡: 大数字 + 标签 + 左侧语义色条。"""

    def __init__(self, label: str, value: Any = 0, color: str = "",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("CardFlat")
        self._color = color or colors()["accent"]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        self.value_label = QLabel(str(value))
        self.value_label.setObjectName("Big")
        self.value_label.setStyleSheet(f"color:{self._color};")
        self.label = QLabel(label)
        self.label.setObjectName("Muted")
        lay.addWidget(self.value_label)
        lay.addWidget(self.label)

    def set_value(self, value: Any, color: Optional[str] = None) -> None:
        self.value_label.setText("—" if value is None else str(value))
        if color:
            self._color = color
            self.value_label.setStyleSheet(f"color:{color};")

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(0, 6, 3, self.height() - 12, QColor(self._color))
        p.end()


class ScoreGauge(QWidget):
    """可制造性评分半环仪表 (自绘, 支持动画)。"""

    def __init__(self, parent: Optional[QWidget] = None, size: int = 190):
        super().__init__(parent)
        self._score: Optional[float] = None
        self._shown = 0.0
        self._risk = ""
        self._verdict = ""
        self.setFixedSize(size, int(size * 0.66))
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._step)

    def set_score(self, score: Optional[float], risk: str = "", verdict: str = "") -> None:
        self._score = None if score is None else float(score)
        self._risk = risk
        self._verdict = verdict
        self._timer.start()

    def _step(self) -> None:
        if self._score is None:
            self._shown = 0.0
            self._timer.stop()
            self.update()
            return
        diff = self._score - self._shown
        if abs(diff) < 0.35:
            self._shown = self._score
            self._timer.stop()
        else:
            self._shown += diff * 0.22
        self.update()

    def _color(self) -> QColor:
        c = colors()
        if self._score is None:
            return QColor(c["fg_mut"])
        return QColor({"危险": c["danger"], "高": SEVERITY_COLORS["高"],
                       "中": c["warn"], "低": c["ok"]}.get(self._risk, c["ok"]))

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = colors()
        w, h = self.width(), self.height()
        pen_w = max(10, int(w * 0.062))
        rect_margin = pen_w / 2 + 2
        square = min(w, (h - rect_margin) * 2)
        x0 = (w - square) / 2 + rect_margin
        y0 = h - square / 2
        arc = QRectF(x0, y0, square - 2 * rect_margin, square - 2 * rect_margin)

        track = QPen(QColor(c["line"]), pen_w, Qt.SolidLine, Qt.RoundCap)
        p.setPen(track)
        p.drawArc(arc, 0, 180 * 16)

        if self._score is not None:
            span = int(180 * 16 * max(0.0, min(100.0, self._shown)) / 100.0)
            val = QPen(self._color(), pen_w, Qt.SolidLine, Qt.RoundCap)
            p.setPen(val)
            p.drawArc(arc, 180 * 16, -span)

        p.setPen(QColor(self._color() if self._score is not None else c["fg_mut"]))
        f = QFont("Segoe UI", int(square * 0.19), QFont.Bold)
        p.setFont(f)
        text = "—" if self._score is None else f"{self._shown:.0f}"
        p.drawText(arc.adjusted(0, 0, 0, -arc.height() * 0.28), Qt.AlignCenter, text)
        p.setPen(QColor(c["fg_mut"]))
        p.setFont(QFont("Segoe UI", max(8, int(square * 0.062))))
        p.drawText(arc.adjusted(0, arc.height() * 0.30, 0, 0), Qt.AlignCenter, "/ 100")
        p.end()


class SeverityBadge(QLabel):
    """严重度徽标。"""

    def __init__(self, severity: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.set_severity(severity)

    def set_severity(self, severity: str) -> None:
        color = SEVERITY_COLORS.get(severity, "#8b8d98")
        self.setText(severity or "—")
        self.setStyleSheet(
            f"background:{color};color:#0b1220;border-radius:9px;"
            f"padding:1px 9px;font-weight:600;font-size:11px;")
        self.setMinimumWidth(42)


class StatusBadge(QLabel):
    """运行状态徽标 (空闲/运行中/完成/失败)。"""

    STATES = {
        "idle": ("空闲", "BadgeIdle"),
        "running": ("分析中", "BadgeWarn"),
        "done": ("完成", "BadgeOk"),
        "error": ("失败", "BadgeBad"),
    }

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.set_state("idle")

    def set_state(self, state: str) -> None:
        text, name = self.STATES.get(state, self.STATES["idle"])
        self.setText(text)
        self.setObjectName(name)
        self.style().unpolish(self)
        self.style().polish(self)


class LogView(QPlainTextEdit):
    """带级别着色的日志视图。"""

    LEVELS = {
        "err": "#f0554d",
        "warn": "#d29922",
        "ok": "#3fb950",
        "info": "#58a6ff",
        "dim": "#7b8896",
    }

    def __init__(self, parent: Optional[QWidget] = None, max_lines: int = 4000):
        super().__init__(parent)
        self.setObjectName("Log")
        self.setReadOnly(True)
        self.setMaximumBlockCount(max_lines)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._formats: Dict[str, Any] = {}
        for name, col in self.LEVELS.items():
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(col))
            self._formats[name] = fmt

    def write(self, text: str, level: str = "") -> None:
        fmt = self._formats.get(level)
        self.setCurrentCharFormat(fmt if fmt is not None else QTextCharFormat())
        self.appendPlainText(text.rstrip("\n"))

    def clear_log(self) -> None:
        self.clear()


class BrandHeader(QWidget):
    """侧栏顶部品牌区。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        from dfm.branding import APP_NAME, APP_SUBTITLE

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 16, 12, 12)
        lay.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(38))
        logo.setFixedSize(38, 38)
        lay.addWidget(logo)
        box = QVBoxLayout()
        box.setSpacing(1)
        title = QLabel(APP_NAME)
        title.setObjectName("BrandTitle")
        sub = QLabel(APP_SUBTITLE)
        sub.setObjectName("BrandSub")
        box.addWidget(title)
        box.addWidget(sub)
        lay.addLayout(box, 1)


class EmptyHint(QWidget):
    """空状态提示 (图标 + 文案 + 可选按钮)。"""

    def __init__(self, text: str, icon_name: str = "info", action_text: str = "",
                 handler: Optional[Callable] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.setAlignment(Qt.AlignCenter)
        self.icon_label = QLabel()
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setPixmap(pixmap(icon_name, colors()["fg_mut"], 42, 1.4))
        lay.addWidget(self.icon_label)
        self.text = QLabel(text)
        self.text.setObjectName("Muted")
        self.text.setAlignment(Qt.AlignCenter)
        self.text.setWordWrap(True)
        lay.addWidget(self.text)
        if action_text:
            btn = button(action_text, "", "Primary", handler)
            wrap = QHBoxLayout()
            wrap.addStretch(1)
            wrap.addWidget(btn)
            wrap.addStretch(1)
            lay.addLayout(wrap)

    def set_text(self, text: str) -> None:
        self.text.setText(text)


def stat_row(cards: List[StatCard]) -> QWidget:
    """把多个 StatCard 排成一行 (等宽自适应)。"""
    host = QWidget()
    grid = QGridLayout(host)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(12)
    for i, c in enumerate(cards):
        grid.addWidget(c, 0, i)
        grid.setColumnStretch(i, 1)
        c.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return host





