"""共享量测函数 — 设计参数自动量测与装配检查**共用同一实现**。

旧问题: ``dfm/core/source.py`` 与 ``dfm/checks/assembly.py`` 各写了一套
"器件本体包围盒 / 最小间距 / 基准点计数", 算法与阈值来源都不同, 导致
报告 Info 页的数值与问题清单结论互相矛盾。现在统一到这里:

  * :func:`component_boxes`      器件本体包围盒 (焊盘聚合近似)
  * :func:`min_component_spacing` 器件本体最小间距 (网格邻近搜索)
  * :func:`component_to_edge`     器件本体到成型线最小距离
  * :func:`fiducials`             基准点识别 (关键字可配, 与规则集一致)
  * :func:`pads_by_refdes`        按位号分组焊盘
  * :func:`min_pad_pitch`         器件内最小引脚间距 (细间距/BGA)
  * :func:`measure_design`        一次性量测全部可比较的设计参数
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dfm.io.brd_model import SpatialGrid, point_poly_edge_dist

DEFAULT_FIDUCIAL_NAMES = ("FID", "MARK", "FP", "BENCH", "基准")


# ============================================================
# 器件本体 (以焊盘包围盒近似; 有 BOM/坐标文件时可由真实外形替换)
# ============================================================
def pads_by_refdes(design) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    for p in getattr(design, "pads", None) or []:
        if p.get("refdes") and p.get("x") is not None:
            out.setdefault(p["refdes"], []).append(p)
    return out


def component_boxes(design) -> List[Dict[str, Any]]:
    """按位号聚合焊盘包围盒作为器件本体近似 (按 cx 排序便于邻近搜索)。"""
    acc: Dict[str, List[float]] = {}
    layer: Dict[str, str] = {}
    for p in getattr(design, "pads", None) or []:
        ref = p.get("refdes")
        if not ref or p.get("x") is None:
            continue
        w = (p.get("pad_w") or p.get("pad_h") or 0.2) / 2.0
        h = (p.get("pad_h") or p.get("pad_w") or 0.2) / 2.0
        b = acc.setdefault(ref, [p["x"], p["y"], p["x"], p["y"], 0])
        b[0] = min(b[0], p["x"] - w)
        b[1] = min(b[1], p["y"] - h)
        b[2] = max(b[2], p["x"] + w)
        b[3] = max(b[3], p["y"] + h)
        b[4] += 1
        if ref not in layer and p.get("layer"):
            layer[ref] = str(p["layer"])
    # 有器件表时补充只有本体无焊盘的器件 (如压接件/屏蔽罩)
    for c in getattr(design, "components", None) or []:
        ref = str(c.get("refdes") or "")
        if ref and ref not in acc and c.get("x") is not None:
            acc[ref] = [c["x"], c["y"], c["x"], c["y"], 0]
            layer[ref] = ""
    out: List[Dict[str, Any]] = []
    for ref, (x0, y0, x1, y1, npad) in acc.items():
        out.append({"refdes": ref, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                    "cx": (x0 + x1) / 2.0, "cy": (y0 + y1) / 2.0,
                    "pins": int(npad), "layer": layer.get(ref, "")})
    out.sort(key=lambda b: b["cx"])
    return out


def box_gap(a: Dict[str, Any], b: Dict[str, Any]) -> Tuple[float, float, float]:
    """两包围盒间距与最近点坐标。"""
    dx = max(0.0, max(a["x0"], b["x0"]) - min(a["x1"], b["x1"]))
    dy = max(0.0, max(a["y0"], b["y0"]) - min(a["y1"], b["y1"]))
    cx = (min(a["x1"], b["x1"]) + max(a["x0"], b["x0"])) / 2.0
    cy = (min(a["y1"], b["y1"]) + max(a["y0"], b["y0"])) / 2.0
    return math.hypot(dx, dy), cx, cy


def _boxes_near(boxes: Sequence[Dict[str, Any]], tol: float):
    """网格邻近搜索产出可能相邻的器件对 (i, j), i<j。"""
    if len(boxes) < 2:
        return
    cell = max(tol * 4.0, 1.0)
    grid = SpatialGrid(cell=cell)
    for i, b in enumerate(boxes):
        grid.insert(b["x0"] - tol, b["y0"] - tol, b["x1"] + tol, b["y1"] + tol, i)
    seen = set()
    for i, b in enumerate(boxes):
        for j in grid.query(b["x0"] - tol, b["y0"] - tol, b["x1"] + tol, b["y1"] + tol):
            if j <= i or (i, j) in seen:
                continue
            seen.add((i, j))
            yield i, j


def min_component_spacing(design, tol: Optional[float] = None
                          ) -> Tuple[Optional[float], Optional[Tuple[str, str]],
                                     Optional[Tuple[float, float]]]:
    """器件本体最小间距 → ``(间距, (refA, refB), (x, y))``。"""
    boxes = component_boxes(design)
    if len(boxes) < 2:
        return None, None, None
    search_tol = float(tol) if tol else 20.0      # 无阈值时按 20mm 邻域搜索
    best: Optional[float] = None
    best_pair: Optional[Tuple[str, str]] = None
    best_pt: Optional[Tuple[float, float]] = None
    for i, j in _boxes_near(boxes, search_tol):
        d, cx, cy = box_gap(boxes[i], boxes[j])
        if best is None or d < best:
            best, best_pair, best_pt = d, (boxes[i]["refdes"], boxes[j]["refdes"]), (cx, cy)
    if best is None:                              # 邻域内无候选 → 全局最小
        for i in range(len(boxes)):
            for j in range(i + 1, min(len(boxes), i + 60)):
                d, cx, cy = box_gap(boxes[i], boxes[j])
                if best is None or d < best:
                    best = d
                    best_pair = (boxes[i]["refdes"], boxes[j]["refdes"])
                    best_pt = (cx, cy)
    return (round(best, 4) if best is not None else None), best_pair, best_pt


def component_to_edge(design) -> Tuple[Optional[float], Optional[str],
                                       Optional[Tuple[float, float]]]:
    """器件本体到成型线最小距离 → ``(距离, refdes, (x, y))``。"""
    poly = list(getattr(design, "outline", None) or [])
    boxes = component_boxes(design)
    if len(poly) < 3 or not boxes:
        return None, None, None
    best: Optional[float] = None
    best_ref = None
    for b in boxes:
        d = min(point_poly_edge_dist(x, y, poly)[0]
                for x, y in ((b["x0"], b["y0"]), (b["x1"], b["y1"]),
                             (b["x0"], b["y1"]), (b["x1"], b["y0"])))
        if best is None or d < best:
            best, best_ref = d, b["refdes"]
    ref = best_ref
    pt = None
    if ref:
        box = next((b for b in boxes if b["refdes"] == ref), None)
        if box:
            pt = (box["cx"], box["cy"])
    return (round(best, 4) if best is not None else None), ref, pt


# ============================================================
# 基准点
# ============================================================
def fiducials(design, names: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """识别基准点 (按位号关键字), 返回 ``{top: [...], bottom: [...], all: [...]}``。"""
    keys = tuple(str(k).upper() for k in (names or DEFAULT_FIDUCIAL_NAMES))
    top: List[dict] = []
    bottom: List[dict] = []
    for c in getattr(design, "components", None) or []:
        ref = str(c.get("refdes", "")).upper()
        if not any(k in ref for k in keys):
            continue
        (bottom if str(c.get("side", "")).lower() in ("bottom", "bot", "b") else top).append(c)
    return {"top": top, "bottom": bottom, "all": top + bottom}


def fiducial_diameter(design, refdes: str) -> Optional[float]:
    sizes = [min(p.get("pad_w") or 0.0, p.get("pad_h") or 0.0) or
             max(p.get("pad_w") or 0.0, p.get("pad_h") or 0.0)
             for p in getattr(design, "pads", None) or []
             if str(p.get("refdes", "")) == refdes]
    sizes = [s for s in sizes if s]
    return min(sizes) if sizes else None


# ============================================================
# 引脚间距 (细间距 / BGA)
# ============================================================
def min_pad_pitch(points: Sequence[Tuple[float, float]]) -> Optional[float]:
    """最小相邻焊盘中心距 (X 排序 + 剪枝扫描, O(n log n))。"""
    n = len(points)
    if n < 2:
        return None
    order = sorted(range(n), key=lambda i: points[i][0])
    best = math.inf
    for a, b in zip(order, order[1:]):
        d = math.hypot(points[b][0] - points[a][0], points[b][1] - points[a][1])
        if 0 < d < best:
            best = d
    for idx, i in enumerate(order):
        x0, y0 = points[i]
        for j in order[idx + 1:]:
            x1, y1 = points[j]
            if x1 - x0 > best:
                break
            d = math.hypot(x1 - x0, y1 - y0)
            if 0 < d < best:
                best = d
    return None if best == math.inf else best


# ============================================================
# 一次性量测 (供 source.derive_params 与报告 Info 页)
# ============================================================
def measure_design(design, fiducial_names: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """量测装配相关设计参数 (与装配检查同源, 保证结论一致)。"""
    out: Dict[str, Any] = {}
    boxes = component_boxes(design)
    if boxes:
        out["comp_count"] = len(boxes)
        spacing, _pair, _pt = min_component_spacing(design)
        if spacing is not None:
            out["min_component_spacing"] = spacing
        edge, _ref, _pt = component_to_edge(design)
        if edge is not None:
            out["component_to_edge"] = edge
    pads = list(getattr(design, "pads", None) or [])
    if pads:
        out["smd_count"] = sum(1 for p in pads if p.get("is_smd"))
        out["pin_count"] = len(pads)
    fid = fiducials(design, fiducial_names)
    if fid["all"] or boxes:
        out["fiducial_count_top"] = len(fid["top"])
        out["fiducial_count_bottom"] = len(fid["bottom"])
    tps = [t for t in (getattr(design, "testpoints", None) or []) if t.get("pad_mm")]
    if tps:
        out["test_pad_diameter"] = round(min(t["pad_mm"] for t in tps), 4)
    # 细间距器件: 取全部器件中最小引脚间距
    pitch: Optional[float] = None
    for _ref, rpads in pads_by_refdes(design).items():
        if len(rpads) < 4:
            continue
        pts = [(p["x"], p["y"]) for p in rpads if p.get("x") is not None]
        p = min_pad_pitch(pts)
        if p is not None and (pitch is None or p < pitch):
            pitch = p
    if pitch is not None:
        out["bga_pitch"] = round(pitch, 4)
    return out
