"""精确净距计算 — 真实形状 (矩形/椭圆/多边形铺铜) 而非外接圆近似。

两级策略:
  1. **广相**: 包围盒 + 容差的扫描剪枝 (纯 Python, 快), 见 :func:`pair_candidates`
  2. **窄相**: shapely 精确距离与最近点, 见 :func:`pair_clearance`

只有进入容差范围的候选对才做 GEOS 运算, 因此十万级图元的板依然可控。
"""
from __future__ import annotations

import math
from typing import Any, Callable, Iterator, List, Optional, Sequence, Tuple

try:
    from shapely.ops import nearest_points
except ImportError as exc:  # pragma: no cover
    raise ImportError("净距计算需要 shapely: pip install shapely") from exc

from dfm.geom.elements import Element
from dfm.geom.shapes import SHAPE_CIRCLE, SHAPE_SEG

#: 广相最多产出的候选对数 (防极端密度下性能塌陷)
MAX_CANDIDATE_PAIRS = 4_000_000


def pair_clearance(ga, gb) -> Tuple[float, float, float]:
    """两几何对象的净距与最近点, 返回 ``(dist, x, y)``。

    重叠/接触时 dist=0, 定位点取包围盒重叠区中心 (廉价);
    不再调用 GEOS ``intersection()`` —— 实测该调用在密集板上占净距总耗时约 1/3。
    """
    try:
        dist = float(ga.distance(gb))
    except Exception:
        return float("inf"), 0.0, 0.0
    if dist > 0:
        try:
            p, q = nearest_points(ga, gb)
            return dist, (p.x + q.x) / 2.0, (p.y + q.y) / 2.0
        except Exception:
            return dist, 0.0, 0.0
    return 0.0, *_bbox_overlap_center(ga, gb)


def _bbox_overlap_center(ga, gb) -> Tuple[float, float]:
    """两几何包围盒重叠区中心 (重叠时的定位点, O(1))。"""
    ax0, ay0, ax1, ay1 = ga.bounds
    bx0, by0, bx1, by1 = gb.bounds
    x0, x1 = max(ax0, bx0), min(ax1, bx1)
    y0, y1 = max(ay0, by0), min(ay1, by1)
    if x1 >= x0 and y1 >= y0:
        return (x0 + x1) / 2.0, (y0 + y1) / 2.0
    ca, cb = ga.centroid, gb.centroid
    return (ca.x + cb.x) / 2.0, (ca.y + cb.y) / 2.0


def element_distance(a: Element, b: Element) -> float:
    """两元素净距 (只算距离, 不算定位点) — 密集判定的主路径。

    圆头图元走解析快路径; 矩形/椭圆/多边形/被负片裁剪过的元素走 GEOS。
    """
    if a.clipped or b.clipped or not (_is_round(a) and _is_round(b)):
        try:
            return float(a.geometry.distance(b.geometry))
        except Exception:
            return float("inf")
    return _analytic_distance(a, b)[0]


def element_location(a: Element, b: Element,
                     dist: Optional[float] = None) -> Tuple[float, float]:
    """定位点 — **仅在确认违规后调用** (避免为每对候选付 GEOS 代价)。

    :param dist: 已算出的净距。批量流程中距离已由 :func:`batch_distances` 得到,
                 传入即可避免重复计算 (重复计算是密集板上的主要浪费)。
    """
    analytic = (not a.clipped and not b.clipped and _is_round(a) and _is_round(b))
    if analytic:
        # 解析路径: 最近点与距离同一次算出, 无需 GEOS
        _d, x, y = _analytic_distance(a, b)
        return x, y
    ga, gb = a.geometry, b.geometry
    try:
        d = float(ga.distance(gb)) if dist is None else float(dist)
        if d > 0:
            p, q = nearest_points(ga, gb)
            return (p.x + q.x) / 2.0, (p.y + q.y) / 2.0
        return _bbox_overlap_center(ga, gb)
    except Exception:
        return (a.shape.x + b.shape.x) / 2.0, (a.shape.y + b.shape.y) / 2.0


def batch_distances(elements: Sequence[Element],
                    pairs: Sequence[Tuple[int, int]]) -> List[float]:
    """批量净距: 解析对走 Python, 其余用 **shapely 向量化** 一次性计算。

    这是间距类检查的性能关键: 逐对调用 GEOS 每对约 5-6us (含 Python 开销),
    向量化后由 C 层循环完成, 密集板上可快一个数量级。
    """
    n = len(pairs)
    out: List[float] = [float("inf")] * n
    if not n:
        return out

    analytic: List[int] = []
    geo_idx: List[int] = []
    geo_a: List[Any] = []
    geo_b: List[Any] = []
    for k, (i, j) in enumerate(pairs):
        a, b = elements[i], elements[j]
        if not a.clipped and not b.clipped and _is_round(a) and _is_round(b):
            analytic.append(k)
        else:
            geo_idx.append(k)
            geo_a.append(a.geometry)
            geo_b.append(b.geometry)

    for k in analytic:
        i, j = pairs[k]
        out[k] = _analytic_distance(elements[i], elements[j])[0]

    if geo_idx:
        try:
            import numpy as np
            import shapely

            arr_a = np.empty(len(geo_a), dtype=object)
            arr_b = np.empty(len(geo_b), dtype=object)
            arr_a[:] = geo_a
            arr_b[:] = geo_b
            dists = shapely.distance(arr_a, arr_b)
            for k, d in zip(geo_idx, dists):
                out[k] = float(d)
        except Exception:
            # 回退: 逐对计算 (功能不受影响, 仅变慢)
            for k in geo_idx:
                i, j = pairs[k]
                try:
                    out[k] = float(elements[i].geometry.distance(elements[j].geometry))
                except Exception:
                    out[k] = float("inf")
    return out


def _analytic_distance(a: Element, b: Element) -> Tuple[float, float, float]:
    """圆头图元 (圆焊盘/过孔/圆头走线) 的解析净距与最近点。"""
    sa, sb = a.shape, b.shape
    ra = (sa.w or 0.0) / 2.0
    rb = (sb.w or 0.0) / 2.0
    if sa.kind == SHAPE_SEG and sb.kind == SHAPE_SEG:
        d, cx, cy = seg_seg_dist((sa.x, sa.y), (sa.x2, sa.y2), (sb.x, sb.y), (sb.x2, sb.y2))
        return max(d - ra - rb, 0.0), cx, cy
    if sa.kind == SHAPE_SEG:
        d, cx, cy = point_seg_dist(sb.x, sb.y, sa.x, sa.y, sa.x2, sa.y2)
        return max(d - ra - rb, 0.0), cx, cy
    if sb.kind == SHAPE_SEG:
        d, cx, cy = point_seg_dist(sa.x, sa.y, sb.x, sb.y, sb.x2, sb.y2)
        return max(d - ra - rb, 0.0), cx, cy
    d = math.hypot(sa.x - sb.x, sa.y - sb.y)
    return max(d - ra - rb, 0.0), (sa.x + sb.x) / 2.0, (sa.y + sb.y) / 2.0


def element_clearance(a: Element, b: Element) -> Tuple[float, float, float]:
    """两元素的净距与定位点 (兼容旧接口; 高频路径请改用
    :func:`batch_distances` + :func:`element_location`)。
    """
    if a.clipped or b.clipped or not (_is_round(a) and _is_round(b)):
        return pair_clearance(a.geometry, b.geometry)
    return _analytic_distance(a, b)


def _is_round(el: Element) -> bool:
    """是否为"圆头"图元 (可用解析距离)。"""
    k = el.shape.kind
    if k == SHAPE_SEG:
        return not el.shape.square_cap
    return k == SHAPE_CIRCLE


def seg_seg_dist(p1, p2, p3, p4) -> Tuple[float, float, float]:
    """两线段最短距离与最近点 (解析实现, 避免为快路径构建几何)。"""
    (x1, y1), (x2, y2), (x3, y3), (x4, y4) = p1, p2, p3, p4
    d1x, d1y = x2 - x1, y2 - y1
    d2x, d2y = x4 - x3, y4 - y3
    denom = d1x * d2y - d1y * d2x
    if abs(denom) > 1e-12:
        t = ((x3 - x1) * d2y - (y3 - y1) * d2x) / denom
        u = ((x3 - x1) * d1y - (y3 - y1) * d1x) / denom
        if 0 <= t <= 1 and 0 <= u <= 1:
            return 0.0, x1 + t * d1x, y1 + t * d1y
    best = (float("inf"), x1, y1)
    for px, py in ((x1, y1), (x2, y2)):
        d, cx, cy = point_seg_dist(px, py, x3, y3, x4, y4)
        if d < best[0]:
            best = (d, cx, cy)
    for px, py in ((x3, y3), (x4, y4)):
        d, cx, cy = point_seg_dist(px, py, x1, y1, x2, y2)
        if d < best[0]:
            best = (d, cx, cy)
    return best


def point_seg_dist(px, py, ax, ay, bx, by) -> Tuple[float, float, float]:
    """点到线段最短距离与最近点。"""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay), ax, ay
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy), cx, cy


def pair_candidates(elements: Sequence[Element], tol: float,
                    filter_fn: Optional[Callable[[Element, Element], bool]] = None,
                    budget: int = MAX_CANDIDATE_PAIRS) -> Iterator[Tuple[int, int]]:
    """扫描剪枝广相: 产出包围盒在 tol 内可能相邻的元素索引对 ``(i, j)``, i<j。

    :param filter_fn: 可选预过滤 (如同层/不同网络), 返回 False 则跳过该对
    """
    n = len(elements)
    if n < 2:
        return
    order = sorted(range(n), key=lambda i: elements[i].minx)
    active: List[int] = []
    count = 0
    for idx in order:
        e = elements[idx]
        xmin = e.minx - tol
        ylo = e.miny - tol
        yhi = e.maxy + tol
        if active:
            active = [a for a in active if elements[a].maxx >= xmin]
        for a in active:
            o = elements[a]
            if o.maxy < ylo or o.miny > yhi:
                continue
            if filter_fn is not None and not filter_fn(o, e):
                continue
            yield a, idx
            count += 1
            if count >= budget:
                return
        active.append(idx)


