"""
BRD-AI: BRD 几何/网表数据模型 (来自 Allegro SKILL 导出 JSON)
=============================================================
读取 src.skill_export 生成的 brd_dfm_export.il 导出的 JSON, 归一化为统一的
BrdDesign 数据结构, 供:
  - src.dfm_geometry : 几何 DFM 校验 (线宽/间距/孔环/孔径/板边/阻焊桥...), 定位 XY
  - src.dfm_netlist  : 连通性派生 -> 开短路 + 网表一致性对比
  - src.fab_viewer   : 版图渲染 + 问题交叉定位 (to_fab_data)

坐标单位: mm (SKILL 端已换算)。所有解析均对缺失字段容错。
"""
import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple

MM_TOL = 1e-4


def _num(v) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _pts(raw) -> List[Tuple[float, float]]:
    out = []
    for p in raw or []:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            x, y = _num(p[0]), _num(p[1])
            if x is not None and y is not None:
                out.append((x, y))
    return out


class BrdDesign:
    """归一化后的 BRD 设计数据"""

    def __init__(self, raw: dict, source_path: str = ""):
        self.raw = raw or {}
        self.source_path = source_path
        self.format = str(self.raw.get("format", ""))
        self.units = str(self.raw.get("units", "mm"))
        self.source_units = str(self.raw.get("source_units", ""))
        self.tool = str(self.raw.get("tool", ""))

        board = self.raw.get("board", {}) or {}
        self.board_name = str(board.get("name", "") or self.raw.get("source", "") or "")
        self.revision = str(board.get("revision", "") or "")
        self.thickness_mm = _num(board.get("thickness_mm"))
        self.layer_count = board.get("layer_count")

        self.outline = _pts(board.get("outline") or self.raw.get("outline"))
        self.stackup = self.raw.get("stackup", []) or []

        self.nets: Dict[str, dict] = {}
        for n in self.raw.get("nets", []) or []:
            name = str(n.get("name", ""))
            if not name:
                continue
            nodes = []
            for nd in n.get("nodes", []) or []:
                x, y = _num(nd.get("x")), _num(nd.get("y"))
                nodes.append({
                    "refdes": str(nd.get("refdes", "") or ""),
                    "pin": str(nd.get("pin", "") or ""),
                    "x": x, "y": y,
                    "layer": str(nd.get("layer", "") or ""),
                })
            self.nets[name] = {"name": name, "nodes": nodes}

        self.components: List[dict] = []
        for c in self.raw.get("components", []) or []:
            self.components.append({
                "refdes": str(c.get("refdes", "") or ""),
                "package": str(c.get("package", "") or ""),
                "x": _num(c.get("x")), "y": _num(c.get("y")),
                "rotation": _num(c.get("rotation")) or 0.0,
                "side": str(c.get("side", "") or ""),
                "pins": c.get("pins"),
            })

        self.clines: List[dict] = []
        for cl in self.raw.get("clines", []) or []:
            pts = _pts(cl.get("points"))
            self.clines.append({
                "net": str(cl.get("net", "") or ""),
                "layer": str(cl.get("layer", "") or ""),
                "width": _num(cl.get("width")),
                "points": pts,
            })

        self.vias: List[dict] = []
        for v in self.raw.get("vias", []) or []:
            self.vias.append({
                "net": str(v.get("net", "") or ""),
                "x": _num(v.get("x")), "y": _num(v.get("y")),
                "drill_mm": _num(v.get("drill_mm")),
                "pad_mm": _num(v.get("pad_mm")),
                "padstack": str(v.get("padstack", "") or ""),
                "start_layer": str(v.get("start_layer", "") or ""),
                "end_layer": str(v.get("end_layer", "") or ""),
                "type": str(v.get("type", "") or "through"),
            })

        self.pads: List[dict] = []
        for p in self.raw.get("pads", []) or []:
            self.pads.append({
                "refdes": str(p.get("refdes", "") or ""),
                "pin": str(p.get("pin", "") or ""),
                "net": str(p.get("net", "") or ""),
                "x": _num(p.get("x")), "y": _num(p.get("y")),
                "layer": str(p.get("layer", "") or ""),
                "padstack": str(p.get("padstack", "") or ""),
                "is_smd": bool(p.get("is_smd")),
                "pad_w": _num(p.get("pad_w")), "pad_h": _num(p.get("pad_h")),
                "drill_mm": _num(p.get("drill_mm")),
            })

        self.shapes: List[dict] = []
        for s in self.raw.get("shapes", []) or []:
            bb = s.get("bbox") or []
            bb = [_num(x) for x in bb] if len(bb) >= 4 else [None, None, None, None]
            self.shapes.append({
                "net": str(s.get("net", "") or ""),
                "layer": str(s.get("layer", "") or ""),
                "bbox": bb,
            })

        self.testpoints: List[dict] = []
        for t in self.raw.get("testpoints", []) or []:
            self.testpoints.append({
                "net": str(t.get("net", "") or ""),
                "refdes": str(t.get("refdes", "") or ""),
                "pin": str(t.get("pin", "") or ""),
                "x": _num(t.get("x")), "y": _num(t.get("y")),
                "layer": str(t.get("layer", "") or ""),
                "pad_mm": _num(t.get("pad_mm")),
            })

        self.drc: List[dict] = []
        for d in self.raw.get("drc", []) or []:
            self.drc.append({
                "type": str(d.get("type", "") or ""),
                "severity": str(d.get("severity", "") or "error"),
                "x": _num(d.get("x")), "y": _num(d.get("y")),
                "layer": str(d.get("layer", "") or ""),
                "text": str(d.get("text", "") or ""),
            })

        # Allegro 连通性 (extracta): 权威开路数据
        self.connectivity_source = str(self.raw.get("connectivity_source", "") or "")
        self.opens: List[dict] = []
        for o in self.raw.get("opens", []) or []:
            self.opens.append({
                "net": str(o.get("net", "") or ""),
                "node1": str(o.get("node1", "") or ""),
                "node2": str(o.get("node2", "") or ""),
                "refdes": str(o.get("refdes", "") or ""),
                "pin": str(o.get("pin", "") or ""),
                "x": _num(o.get("x")), "y": _num(o.get("y")),
            })
        self.net_avg_width_mm: Dict[str, float] = self.raw.get("net_avg_width_mm", {}) or {}
        self.etch_width_is_average: bool = bool(self.raw.get("etch_width_is_average", False))

        self.bbox = self._compute_bbox()
        self._layers = None

    # ---------------- 派生属性 ----------------
    def _compute_bbox(self) -> Tuple[float, float, float, float]:
        xs: List[float] = []
        ys: List[float] = []
        for x, y in self.outline:
            xs.append(x); ys.append(y)
        for cl in self.clines:
            for x, y in cl["points"]:
                xs.append(x); ys.append(y)
        for v in self.vias:
            if v["x"] is not None:
                xs.append(v["x"]); ys.append(v["y"])
        for p in self.pads:
            if p["x"] is not None:
                xs.append(p["x"]); ys.append(p["y"])
        for c in self.components:
            if c["x"] is not None:
                xs.append(c["x"]); ys.append(c["y"])
        if not xs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(ys), max(xs), max(ys))

    def copper_layers(self) -> List[str]:
        """出现铜箔的层名 (按 stackup 顺序优先)"""
        if self._layers is not None:
            return self._layers
        seen = []
        names = set()
        for st in self.stackup:
            nm = str(st.get("layer", "") or "")
            if nm and nm not in names:
                names.add(nm); seen.append(nm)
        for coll in (self.clines, self.pads, self.vias, self.shapes):
            for it in coll:
                nm = it.get("layer", "")
                if nm and nm not in names:
                    names.add(nm); seen.append(nm)
        self._layers = seen
        return seen

    def net_names(self) -> List[str]:
        return sorted(self.nets.keys())

    def is_power_net(self, name: str) -> bool:
        u = name.upper()
        return (u.startswith(("GND", "AGND", "PGND", "DGND", "VCC", "VDD", "VBAT"))
                or u.endswith(("GND", "_PWR")) or "V" in u[:1])

    def stats(self) -> dict:
        return {
            "net_count": len(self.nets),
            "node_count": sum(len(n["nodes"]) for n in self.nets.values()),
            "component_count": len(self.components),
            "cline_count": len(self.clines),
            "via_count": len(self.vias),
            "pad_count": len(self.pads),
            "shape_count": len(self.shapes),
            "testpoint_count": len(self.testpoints),
            "drc_count": len(self.drc),
            "layer_count": self.layer_count or len(self.copper_layers()),
            "board_thickness_mm": self.thickness_mm,
            "board_size": [round(self.bbox[2] - self.bbox[0], 3),
                           round(self.bbox[3] - self.bbox[1], 3)],
        }


def load_brd_export(path: str) -> BrdDesign:
    """读取 SKILL 导出的 JSON -> BrdDesign"""
    with open(path, "r", encoding="utf-8-sig") as f:
        raw = json.load(f)
    return BrdDesign(raw, source_path=path)


# ============================================================
# 空间索引 (均匀网格) — 供间距/连通性查询
# ============================================================
class SpatialGrid:
    def __init__(self, cell: float = 1.0):
        self.cell = max(cell, 1e-6)
        self.cells: Dict[Tuple[int, int], List[Any]] = {}

    def _range(self, minx, miny, maxx, maxy):
        c = self.cell
        x0 = int(math.floor(minx / c)); x1 = int(math.floor(maxx / c))
        y0 = int(math.floor(miny / c)); y1 = int(math.floor(maxy / c))
        return x0, y0, x1, y1

    def insert(self, minx, miny, maxx, maxy, item):
        x0, y0, x1, y1 = self._range(minx, miny, maxx, maxy)
        for cx in range(x0, x1 + 1):
            for cy in range(y0, y1 + 1):
                self.cells.setdefault((cx, cy), []).append(item)

    def query(self, minx, miny, maxx, maxy) -> List[Any]:
        x0, y0, x1, y1 = self._range(minx, miny, maxx, maxy)
        seen = set()
        out = []
        for cx in range(x0, x1 + 1):
            for cy in range(y0, y1 + 1):
                for it in self.cells.get((cx, cy), ()):
                    k = id(it)
                    if k not in seen:
                        seen.add(k)
                        out.append(it)
        return out


def sweep_pairs(items: List[Any], tol: float = 0.0, budget: int = 8_000_000):
    """扫描剪枝 (sweep-and-prune) 广相: 产出包围盒在 X/Y 上于 tol 内可能重叠的
    元素索引对 (i, j)。items 元素需有 .minx/.miny/.maxx/.maxy 属性。
    相比均匀网格, 对含超宽走线/铺铜的板不会因单元爆炸而退化, 每对仅产出一次。
    超过 budget 对则提前停止 (防止极端密度下的性能塌陷)。
    """
    n = len(items)
    if n < 2:
        return
    order = sorted(range(n), key=lambda i: items[i].minx)
    active: List[int] = []
    count = 0
    for idx in order:
        e = items[idx]
        xmin = e.minx - tol
        ylo = e.miny - tol
        yhi = e.maxy + tol
        # 移除 X 方向已不可能重叠的活跃元素
        if active:
            active = [a for a in active if items[a].maxx >= xmin]
        for a in active:
            o = items[a]
            if o.maxy < ylo or o.miny > yhi:
                continue
            yield a, idx
            count += 1
            if count >= budget:
                return
        active.append(idx)


# ============================================================
# 几何距离工具
# ============================================================
def point_seg_dist(px, py, ax, ay, bx, by) -> Tuple[float, float, float]:
    """点到线段最短距离, 返回 (dist, 最近点x, 最近点y)"""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay), ax, ay
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy), cx, cy


def seg_seg_dist(p1, p2, p3, p4) -> Tuple[float, float, float]:
    """两线段最短距离, 返回 (dist, 最近点x, 最近点y) (近似取端点/交点)"""
    (x1, y1), (x2, y2), (x3, y3), (x4, y4) = p1, p2, p3, p4
    d1x, d1y = x2 - x1, y2 - y1
    d2x, d2y = x4 - x3, y4 - y3
    denom = d1x * d2y - d1y * d2x
    if abs(denom) > 1e-12:
        t = ((x3 - x1) * d2y - (y3 - y1) * d2x) / denom
        u = ((x3 - x1) * d1y - (y3 - y1) * d1x) / denom
        if 0 <= t <= 1 and 0 <= u <= 1:
            ix, iy = x1 + t * d1x, y1 + t * d1y
            return 0.0, ix, iy
    best = (float("inf"), x1, y1)
    for (px, py) in (p1, p2):
        d, cx, cy = point_seg_dist(px, py, x3, y3, x4, y4)
        if d < best[0]:
            best = (d, cx, cy)
    for (px, py) in (p3, p4):
        d, cx, cy = point_seg_dist(px, py, x1, y1, x2, y2)
        if d < best[0]:
            best = (d, cx, cy)
    return best


def point_in_polygon(px, py, poly: List[Tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def point_poly_edge_dist(px, py, poly: List[Tuple[float, float]]) -> Tuple[float, float, float]:
    """点到多边形边的最短距离, 返回 (dist, 最近点x, 最近点y)"""
    best = (float("inf"), px, py)
    n = len(poly)
    if n == 0:
        return best
    if n == 1:
        return math.hypot(px - poly[0][0], py - poly[0][1]), poly[0][0], poly[0][1]
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        d, cx, cy = point_seg_dist(px, py, ax, ay, bx, by)
        if d < best[0]:
            best = (d, cx, cy)
    return best


# ============================================================
# 转为 fab_data 结构 (供 fab_viewer / render_fab_svg 渲染 + 交叉定位)
# ============================================================
def to_fab_data(design: BrdDesign) -> List[dict]:
    """把 BrdDesign 转成 fab_viewer 认识的结构:
       - 一个 kind='odbpp' 的 features 层集合 (draw/flash)
       - 一个 kind='drill' 的 holes 集合 (过孔/插件孔)
    """
    features: List[dict] = []
    for cl in design.clines:
        pts = cl["points"]
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            features.append({
                "type": "draw", "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                "layer": cl["layer"] or "COPPER", "net": cl["net"],
                "width": cl["width"],
            })
    for p in design.pads:
        if p["x"] is None:
            continue
        w = p["pad_w"] or p["pad_h"] or 0.3
        features.append({
            "type": "flash", "x1": p["x"], "y1": p["y"], "x2": p["x"], "y2": p["y"],
            "layer": p["layer"] or "COPPER", "net": p["net"], "width": w,
            "refdes": p["refdes"], "pin": p["pin"],
        })
    for v in design.vias:
        if v["x"] is None:
            continue
        w = v["pad_mm"] or 0.5
        features.append({
            "type": "flash", "x1": v["x"], "y1": v["y"], "x2": v["x"], "y2": v["y"],
            "layer": v["start_layer"] or "COPPER", "net": v["net"], "width": w,
        })
    for s in design.shapes:
        bb = s["bbox"]
        if bb[0] is None:
            continue
        features.append({
            "type": "region", "points": [(bb[0], bb[1]), (bb[2], bb[1]),
                                          (bb[2], bb[3]), (bb[0], bb[3])],
            "x1": bb[0], "y1": bb[1], "x2": bb[2], "y2": bb[3],
            "layer": s["layer"] or "COPPER", "net": s["net"],
        })

    minx, miny, maxx, maxy = design.bbox
    copper = {
        "kind": "odbpp", "name": design.board_name or "BRD",
        "file": design.source_path, "unit": "mm",
        "features": features, "feature_count": len(features),
        "bbox": [minx, miny, maxx, maxy],
        "nets": {name: sorted({f"{nd['refdes']}.{nd['pin']}" for nd in n["nodes"]
                               if nd["refdes"]})
                 for name, n in design.nets.items()},
        "attributes_present": True,
    }

    holes: List[dict] = []
    for v in design.vias:
        if v["x"] is None or not v["drill_mm"]:
            continue
        holes.append({"x": v["x"], "y": v["y"], "diameter_mm": v["drill_mm"],
                      "plating": "PTH", "tool": 1})
    for p in design.pads:
        if p["x"] is None or not p["drill_mm"] or p["drill_mm"] <= 0:
            continue
        holes.append({"x": p["x"], "y": p["y"], "diameter_mm": p["drill_mm"],
                      "plating": "PTH", "tool": 1})
    drill = {
        "kind": "drill", "name": "DRILL", "file": design.source_path, "unit": "mm",
        "plating": "PTH", "holes": holes, "hole_count": len(holes),
        "bbox": [minx, miny, maxx, maxy],
    }
    return [copper, drill]
