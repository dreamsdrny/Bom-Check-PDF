"""版图视图 (QGraphicsView) — 渲染 Gerber/ODB++/BRD 几何并与问题清单交叉定位。

性能策略: 同一图层内相同线宽的图元合并为一个 QPainterPath 图元,
因此十万级图元的板子也只有几十个场景图元, 缩放平移保持流畅。

交互:
  * 滚轮缩放 (以光标为中心) / 中键或右键拖动平移 / 双击适应窗口
  * 单击问题标记 → 发出 issue_clicked(原始索引)
  * 外部调用 :meth:`BoardView.select_issue` → 高亮并居中该问题
  * 鼠标移动发出 cursor_moved(x_mm, y_mm)
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QPainter, QPainterPath, QPen,
                           QPolygonF)
from PySide6.QtWidgets import (QGraphicsEllipseItem, QGraphicsItem, QGraphicsScene,
                               QGraphicsView)

from dfm.core.issue import SEVERITY_COLOR
from dfm.report.svg import collect_bbox, iter_primitives, layer_color

MAX_MARKERS = 6000
MAX_PATH_SEGMENTS = 400000


class BoardScene(QGraphicsScene):
    """版图场景: 坐标系为 mm, Y 轴向上 (存储时取 -y)。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackgroundBrush(QBrush(QColor("#0b0f14")))
        self._layer_items: Dict[str, List[QGraphicsItem]] = {}
        self._z_order: Dict[str, int] = {}
        self._marker_items: List[QGraphicsEllipseItem] = []
        self._selected = -1

    # ---------- 构建 ----------
    def clear_board(self) -> None:
        self.clear()
        self._layer_items.clear()
        self._z_order.clear()
        self._marker_items.clear()
        self._selected = -1

    def build(self, fab_data: List[dict]) -> None:
        self.clear_board()
        if not fab_data:
            return
        bbox = collect_bbox(fab_data)
        if bbox:
            minx, miny, maxx, maxy = bbox
            rect = QRectF(minx, -maxy, maxx - minx, maxy - miny)
            item = self.addRect(rect, QPen(QColor("#5c6b7a"), 0.05),
                                QBrush(QColor("#141a21")))
            item.setZValue(-100)
            self.setSceneRect(rect.adjusted(-rect.width() * 0.05, -rect.height() * 0.05,
                                            rect.width() * 0.05, rect.height() * 0.05))
        self._build_layers(fab_data)
        self._build_drills(fab_data)

    def _build_layers(self, fab_data: List[dict]) -> None:
        # (layer, kind, width) -> QPainterPath
        strokes: Dict[Tuple[str, float], QPainterPath] = {}
        fills: Dict[str, QPainterPath] = {}
        regions: Dict[str, QPainterPath] = {}
        seg_count = 0
        for d, p in iter_primitives(fab_data):
            if seg_count >= MAX_PATH_SEGMENTS:
                break
            layer = str(p.get("layer") or d.get("name") or "LAYER")
            t = p.get("type")
            if t in ("draw", "arc", "region_start"):
                x1, y1, x2, y2 = p.get("x1"), p.get("y1"), p.get("x2"), p.get("y2")
                if None in (x1, y1, x2, y2):
                    continue
                w = float(p.get("width") or 0.0)
                key = (layer, round(max(w, 0.01), 4))
                path = strokes.setdefault(key, QPainterPath())
                path.moveTo(float(x1), -float(y1))
                path.lineTo(float(x2), -float(y2))
                seg_count += 1
            elif t == "flash":
                x, y = p.get("x1"), p.get("y1")
                if x is None or y is None:
                    continue
                r = max(float(p.get("width") or 0.2) / 2.0, 0.01)
                path = fills.setdefault(layer, QPainterPath())
                path.addEllipse(QPointF(float(x), -float(y)), r, r)
                seg_count += 1
            elif t == "region":
                pts = p.get("points") or []
                if len(pts) < 3:
                    continue
                poly = QPolygonF([QPointF(float(q[0]), -float(q[1])) for q in pts])
                path = regions.setdefault(layer, QPainterPath())
                path.addPolygon(poly)
                path.closeSubpath()
                seg_count += len(pts)

        for (layer, width), path in strokes.items():
            color = QColor(layer_color(layer))
            item = self.addPath(path, QPen(color, width, Qt.SolidLine, Qt.RoundCap,
                                           Qt.RoundJoin))
            item.setZValue(self._z(layer))
            self._layer_items.setdefault(layer, []).append(item)
        for layer, path in fills.items():
            color = QColor(layer_color(layer))
            item = self.addPath(path, QPen(Qt.NoPen), QBrush(color))
            item.setZValue(self._z(layer) + 0.1)
            self._layer_items.setdefault(layer, []).append(item)
        for layer, path in regions.items():
            color = QColor(layer_color(layer))
            color.setAlphaF(0.42)
            item = self.addPath(path, QPen(Qt.NoPen), QBrush(color))
            item.setZValue(self._z(layer) + 0.2)
            self._layer_items.setdefault(layer, []).append(item)

    def _build_drills(self, fab_data: List[dict]) -> None:
        path = QPainterPath()
        n = 0
        for d in fab_data:
            if d.get("kind") != "drill":
                continue
            for h in d.get("holes") or []:
                if h.get("x") is None:
                    continue
                r = max(float(h.get("diameter_mm") or 0.3) / 2.0, 0.01)
                path.addEllipse(QPointF(float(h["x"]), -float(h["y"])), r, r)
                n += 1
                if n >= MAX_PATH_SEGMENTS:
                    break
        if n:
            item = self.addPath(path, QPen(QColor("#9fb0c0"), 0.02),
                                QBrush(QColor("#0b0e11")))
            item.setZValue(50)
            self._layer_items.setdefault("__drill__", []).append(item)

    def _z(self, layer: str) -> float:
        """按图层顺序递增 Z, 保证叠放次序稳定。"""
        if layer not in self._z_order:
            self._z_order[layer] = len(self._z_order)
        return 1.0 + self._z_order[layer] * 0.1

    # ---------- 问题标记 ----------
    def add_markers(self, issues: List[dict]) -> None:
        for item in self._marker_items:
            self.removeItem(item)
        self._marker_items.clear()
        scale = max(self.sceneRect().width(), self.sceneRect().height()) or 100.0
        r = max(scale / 260.0, 0.12)
        count = 0
        for i, it in enumerate(issues or []):
            x, y = it.get("x"), it.get("y")
            if x is None or y is None:
                continue
            color = QColor("#" + SEVERITY_COLOR.get(it.get("severity"), "999999"))
            item = self.addEllipse(QRectF(float(x) - r, -float(y) - r, 2 * r, 2 * r),
                                   QPen(QColor("#ffffff"), r * 0.28), QBrush(color))
            item.setZValue(200)
            item.setData(0, i)
            item.setFlag(QGraphicsItem.ItemIsSelectable, True)
            item.setToolTip(f"[{it.get('severity')}] {it.get('code')} {it.get('rule')}\n"
                            f"{it.get('description', '')}\nX={x:.3f} Y={y:.3f} mm")
            self._marker_items.append(item)
            count += 1
            if count >= MAX_MARKERS:
                break
        self._selected = -1

    def marker_radius(self) -> float:
        scale = max(self.sceneRect().width(), self.sceneRect().height()) or 100.0
        return max(scale / 260.0, 0.12)

    def set_selected(self, index: int) -> None:
        r = self.marker_radius()
        for item in self._marker_items:
            i = item.data(0)
            on = (i == index)
            item.setPen(QPen(QColor("#ffffff"), r * (0.5 if on else 0.28)))
            item.setZValue(300 if on else 200)
            item.setScale(1.7 if on else 1.0)
        self._selected = index

    def marker_pos(self, index: int) -> Optional[QPointF]:
        for item in self._marker_items:
            if item.data(0) == index:
                return item.rect().center()
        return None

    # ---------- 图层显隐 ----------
    def layers(self) -> List[str]:
        return [name for name in self._layer_items if name != "__drill__"]

    def set_layer_visible(self, layer: str, visible: bool) -> None:
        for item in self._layer_items.get(layer, []):
            item.setVisible(visible)

    def set_all_layers_visible(self, visible: bool) -> None:
        for items in self._layer_items.values():
            for item in items:
                item.setVisible(visible)

    def visible_layers(self) -> List[str]:
        out = []
        for name, items in self._layer_items.items():
            if items and items[0].isVisible():
                out.append(name)
        return out


class BoardView(QGraphicsView):
    """版图视图控件。"""

    issue_clicked = Signal(int)
    cursor_moved = Signal(float, float)
    zoom_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = BoardScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setBackgroundBrush(QBrush(QColor("#0b0f14")))
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._zoom = 1.0
        self._base_scale = 1.0
        self.setMouseTracking(True)

    # ---------- 数据 ----------
    def set_board(self, fab_data: List[dict], issues: Optional[List[dict]] = None) -> None:
        self._scene.build(fab_data or [])
        self._scene.add_markers(issues or [])
        self.fit()

    def set_issues(self, issues: List[dict]) -> None:
        self._scene.add_markers(issues or [])

    def has_board(self) -> bool:
        return bool(self._scene.items())

    def layer_names(self) -> List[str]:
        return self._scene.layers()

    def set_layer_visible(self, layer: str, visible: bool) -> None:
        self._scene.set_layer_visible(layer, visible)

    def set_all_layers_visible(self, visible: bool) -> None:
        self._scene.set_all_layers_visible(visible)

    # ---------- 视图控制 ----------
    def fit(self) -> None:
        rect = self._scene.sceneRect()
        if rect.isEmpty():
            return
        self.fitInView(rect.adjusted(-rect.width() * 0.02, -rect.height() * 0.02,
                                     rect.width() * 0.02, rect.height() * 0.02),
                       Qt.KeepAspectRatio)
        self._base_scale = self.transform().m11() or 1.0
        self._zoom = 1.0
        self.zoom_changed.emit(self._zoom)

    def zoom_by(self, factor: float) -> None:
        new_zoom = max(0.02, min(400.0, self._zoom * factor))
        factor = new_zoom / self._zoom
        self._zoom = new_zoom
        self.scale(factor, factor)
        self.zoom_changed.emit(self._zoom)

    def zoom_level(self) -> float:
        return self._zoom

    def select_issue(self, index: int, center: bool = True) -> None:
        self._scene.set_selected(index)
        if not center:
            return
        pos = self._scene.marker_pos(index)
        if pos is None:
            return
        rect = self._scene.sceneRect()
        span = max(rect.width(), rect.height()) / 12.0
        self.centerOn(pos)
        target = QRectF(pos.x() - span, pos.y() - span, 2 * span, 2 * span)
        self.fitInView(target, Qt.KeepAspectRatio)
        self._base_scale = self.transform().m11() or self._base_scale
        self._zoom = (self.transform().m11() / self._base_scale) if self._base_scale else 1.0
        self.zoom_changed.emit(self._zoom)

    def reset_zoom_to(self, zoom: float) -> None:
        self.fit()
        if abs(zoom - 1.0) > 1e-6:
            self.zoom_by(zoom)

    # ---------- 事件 ----------
    def wheelEvent(self, event) -> None:  # noqa: N802
        self.zoom_by(1.18 if event.angleDelta().y() > 0 else 1 / 1.18)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        p = self.mapToScene(event.position().toPoint())
        self.cursor_moved.emit(round(p.x(), 3), round(-p.y(), 3))
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if item is not None and item.data(0) is not None and item.zValue() >= 200:
                self.issue_clicked.emit(int(item.data(0)))
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.fit()
