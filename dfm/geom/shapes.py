"""真实几何形状 (mm) → shapely geometry。

支持的形状::

    seg     线段 + 线宽 (圆头/平头)
    circle  圆 (焊盘/过孔/Mark)
    rect    矩形 (可旋转) —— 连接器/屏蔽罩等长条焊盘的真实形状
    round   圆角矩形 / 椭圆 (Gerber O 型光圈: 两端半圆 + 中间矩形)
    poly    多边形 (铺铜 region、P 型光圈、异形焊盘)
    arc     圆弧 (按弦高离散为折线, 默认 3°/段)

统一约定: 坐标 mm, Y 向上; 旋转角逆时针为正 (度)。
"""
from __future__ import annotations

import math
from typing import Any, List, Optional, Sequence, Tuple

try:
    from shapely.affinity import rotate as _rotate
    from shapely.geometry import LineString, Point, Polygon
    from shapely.ops import unary_union
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Evan DFM 的几何内核需要 shapely 2.x: pip install shapely") from exc

SHAPE_SEG = "seg"
SHAPE_CIRCLE = "circle"
SHAPE_RECT = "rect"
SHAPE_ROUND = "round"
SHAPE_POLY = "poly"
SHAPE_ARC = "arc"

ARC_STEP_DEG = 3.0
MIN_SEG_LEN = 1e-9


class Shape:
    """一个几何形状 (不可变语义; ``geom`` 惰性计算并缓存)。"""

    __slots__ = (
        "_bbox",
        "_geom",
        "ccw",
        "cx",
        "cy",
        "h",
        "kind",
        "points",
        "rot",
        "square_cap",
        "w",
        "x",
        "x2",
        "y",
        "y2",
    )

    def __init__(self, kind: str, x: float = 0.0, y: float = 0.0,
                 x2: Optional[float] = None, y2: Optional[float] = None,
                 w: float = 0.0, h: Optional[float] = None, rot: float = 0.0,
                 points: Optional[Sequence[Tuple[float, float]]] = None,
                 ccw: Optional[bool] = None,
                 cx: Optional[float] = None, cy: Optional[float] = None,
                 square_cap: bool = False):
        self.kind = kind
        self.x = float(x)
        self.y = float(y)
        self.x2 = None if x2 is None else float(x2)
        self.y2 = None if y2 is None else float(y2)
        self.w = float(w or 0.0)
        self.h = float(h if h is not None else (w or 0.0))
        self.rot = float(rot or 0.0)
        self.points: List[Tuple[float, float]] = (
            [(float(p[0]), float(p[1])) for p in points] if points else [])
        self.ccw = ccw
        self.cx = None if cx is None else float(cx)
        self.cy = None if cy is None else float(cy)
        self.square_cap = bool(square_cap)
        self._geom = None
        self._bbox: Optional[Tuple[float, float, float, float]] = None

    # ---------------- 构造便捷方法 ----------------
    @classmethod
    def segment(cls, x1, y1, x2, y2, width, square_cap: bool = False) -> Shape:
        return cls(SHAPE_SEG, x1, y1, x2, y2, w=width, square_cap=square_cap)

    @classmethod
    def circle(cls, x, y, diameter) -> Shape:
        return cls(SHAPE_CIRCLE, x, y, w=diameter)

    @classmethod
    def rect(cls, x, y, w, h, rot: float = 0.0) -> Shape:
        return cls(SHAPE_RECT, x, y, w=w, h=h, rot=rot)

    @classmethod
    def oblong(cls, x, y, w, h, rot: float = 0.0) -> Shape:
        """Gerber O 型光圈 / 椭圆形焊盘"""
        return cls(SHAPE_ROUND, x, y, w=w, h=h, rot=rot)

    @classmethod
    def polygon(cls, points) -> Shape:
        return cls(SHAPE_POLY, points=list(points))

    @classmethod
    def arc(cls, x1, y1, x2, y2, cx, cy, ccw: bool, width) -> Shape:
        return cls(SHAPE_ARC, x1, y1, x2, y2, w=width, ccw=ccw, cx=cx, cy=cy)

    # ---------------- 几何 ----------------
    def _build(self):
        k = self.kind
        if k == SHAPE_SEG:
            x2 = self.x2 if self.x2 is not None else self.x
            y2 = self.y2 if self.y2 is not None else self.y
            if abs(x2 - self.x) < MIN_SEG_LEN and abs(y2 - self.y) < MIN_SEG_LEN:
                return Point(self.x, self.y).buffer(max(self.w / 2.0, 1e-6), quad_segs=6)
            cap = 2 if self.square_cap else 1          # 1=round, 2=flat
            line = LineString([(self.x, self.y), (x2, y2)])
            if self.square_cap and self.w > 0:
                # 平头需两端各外延半宽, 才等价于 Gerber 矩形端头
                dx, dy = x2 - self.x, y2 - self.y
                length = math.hypot(dx, dy) or 1.0
                ex, ey = dx / length * self.w / 2.0, dy / length * self.w / 2.0
                line = LineString([(self.x - ex, self.y - ey), (x2 + ex, y2 + ey)])
            return line.buffer(max(self.w / 2.0, 1e-9), quad_segs=6, cap_style=cap)
        if k == SHAPE_CIRCLE:
            return Point(self.x, self.y).buffer(max(self.w / 2.0, 1e-6), quad_segs=12)
        if k == SHAPE_RECT:
            w, h = max(self.w, 1e-6), max(self.h, 1e-6)
            box = Polygon([(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)])
            g = _rotate(box, self.rot, origin=(0, 0)) if self.rot else box
            return _translate(g, self.x, self.y)
        if k == SHAPE_ROUND:
            w, h = max(self.w, 1e-6), max(self.h, 1e-6)
            r = min(w, h) / 2.0
            if abs(w - h) < 1e-9:
                g = Point(0, 0).buffer(r, quad_segs=12)
            else:
                long_axis = max(w, h) - 2 * r
                if w >= h:
                    body = Polygon([(-long_axis / 2, -r), (long_axis / 2, -r),
                                    (long_axis / 2, r), (-long_axis / 2, r)])
                    g = unary_union([body, Point(-long_axis / 2, 0).buffer(r, quad_segs=10),
                                     Point(long_axis / 2, 0).buffer(r, quad_segs=10)])
                else:
                    body = Polygon([(-r, -long_axis / 2), (r, -long_axis / 2),
                                    (r, long_axis / 2), (-r, long_axis / 2)])
                    g = unary_union([body, Point(0, -long_axis / 2).buffer(r, quad_segs=10),
                                     Point(0, long_axis / 2).buffer(r, quad_segs=10)])
            g = _rotate(g, self.rot, origin=(0, 0)) if self.rot else g
            return _translate(g, self.x, self.y)
        if k == SHAPE_POLY:
            pts = self.points
            if len(pts) < 3:
                if not pts:
                    return Point(self.x, self.y).buffer(1e-6)
                return LineString(pts).buffer(1e-6)
            poly = Polygon(pts)
            return poly if poly.is_valid else poly.buffer(0)
        if k == SHAPE_ARC:
            pts = self.arc_points()
            if len(pts) < 2:
                return Point(self.x, self.y).buffer(max(self.w / 2.0, 1e-6))
            return LineString(pts).buffer(max(self.w / 2.0, 1e-9), quad_segs=6)
        raise ValueError(f"未知形状: {k}")

    def arc_points(self, step_deg: float = ARC_STEP_DEG) -> List[Tuple[float, float]]:
        """圆弧离散为折线 (支持多象限 G75 与单象限 G74 语义)。"""
        # 注意: 终点坐标可能为 0, 不能用 `or` 回退 (会把 0 当成缺失)
        ex = self.x if self.x2 is None else self.x2
        ey = self.y if self.y2 is None else self.y2
        cx = self.x if self.cx is None else self.cx
        cy = self.y if self.cy is None else self.cy
        r0 = math.hypot(self.x - cx, self.y - cy)
        r1 = math.hypot(ex - cx, ey - cy)
        r = (r0 + r1) / 2.0
        if r <= 0:
            return [(self.x, self.y), (ex, ey)]
        a0 = math.degrees(math.atan2(self.y - cy, self.x - cx))
        a1 = math.degrees(math.atan2(ey - cy, ex - cx))
        sweep = a1 - a0
        if self.ccw:                       # G02 顺时针: 角度递减
            while sweep > 0:
                sweep -= 360.0
        else:                              # G03 逆时针: 角度递增
            while sweep < 0:
                sweep += 360.0
        if abs(sweep) < 1e-9:
            sweep = -360.0 if self.ccw else 360.0
        steps = max(2, int(abs(sweep) / max(step_deg, 0.5)))
        out = []
        for i in range(steps + 1):
            a = math.radians(a0 + sweep * i / steps)
            out.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        return out

    @property
    def geom(self):
        if self._geom is None:
            self._geom = self._build()
        return self._geom

    @property
    def bbox(self) -> Tuple[float, float, float, float]:
        """包围盒 — **解析计算**, 不构建 shapely 几何 (大板性能关键)。"""
        if self._bbox is not None:
            return self._bbox
        k = self.kind
        if k == SHAPE_SEG:
            x2 = self.x2 if self.x2 is not None else self.x
            y2 = self.y2 if self.y2 is not None else self.y
            r = max(self.w / 2.0, 0.0)
            self._bbox = (min(self.x, x2) - r, min(self.y, y2) - r,
                          max(self.x, x2) + r, max(self.y, y2) + r)
        elif k == SHAPE_CIRCLE:
            r = max(self.w / 2.0, 1e-6)
            self._bbox = (self.x - r, self.y - r, self.x + r, self.y + r)
        elif k in (SHAPE_RECT, SHAPE_ROUND):
            half = self._corner_half_extent()
            self._bbox = (self.x - half[0], self.y - half[1],
                          self.x + half[0], self.y + half[1])
        elif k == SHAPE_POLY:
            pts = self.points or [(self.x, self.y)]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            self._bbox = (min(xs), min(ys), max(xs), max(ys))
        elif k == SHAPE_ARC:
            pts = self.arc_points(step_deg=15.0)
            r = max(self.w / 2.0, 0.0)
            xs = [p[0] for p in pts] or [self.x]
            ys = [p[1] for p in pts] or [self.y]
            self._bbox = (min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r)
        else:
            self._bbox = (self.x, self.y, self.x, self.y)
        return self._bbox

    def _corner_half_extent(self) -> Tuple[float, float]:
        """旋转矩形/椭圆的外接半宽半高 (解析, 用于包围盒)。"""
        w = max(self.w, 1e-6) / 2.0
        h = max(self.h, 1e-6) / 2.0
        if self.kind == SHAPE_ROUND:
            # 椭圆外包: 用半轴近似 (长短轴各取一半)
            w, h = max(w, h), min(w, h)
        if not self.rot:
            return (w, h)
        a = math.radians(self.rot)
        ca, sa = abs(math.cos(a)), abs(math.sin(a))
        return (w * ca + h * sa, w * sa + h * ca)

    def area(self) -> float:
        try:
            return float(self.geom.area)
        except Exception:
            return 0.0

    def is_empty(self) -> bool:
        try:
            return bool(self.geom.is_empty)
        except Exception:
            return True

    def describe(self) -> str:
        if self.kind == SHAPE_SEG:
            return f"seg(w={self.w:.3f})"
        if self.kind in (SHAPE_CIRCLE, SHAPE_ROUND):
            return f"{self.kind}(d={self.w:.3f}x{self.h:.3f})"
        if self.kind == SHAPE_RECT:
            return f"rect({self.w:.3f}x{self.h:.3f},rot={self.rot:.1f})"
        return f"{self.kind}({len(self.points)}pts)"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Shape {self.describe()} @({self.x:.3f},{self.y:.3f})>"


def _translate(g, dx: float, dy: float):
    from shapely.affinity import translate

    return translate(g, dx, dy)



def to_points(raw: Any) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    for p in raw or []:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            try:
                out.append((float(p[0]), float(p[1])))
            except (TypeError, ValueError):
                continue
    return out
