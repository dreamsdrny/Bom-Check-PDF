"""BoardGeometry — 一块板的共享几何模型 (构建一次, 全部检查复用)。

旧实现的问题: 制造 DFM 与开短路各自建一份元素表 (两份 40 万元素上限),
且都用"线段 + 半径"近似 → 重复计算 + 精度不足。

本模块统一负责:
  1. 从归一设计模型 (:class:`dfm.io.brd_model.BrdDesign`) 构造**真实形状**元素
  2. 逐层做**极性合成** (见 :mod:`dfm.geom.polarity`)
  3. 建立层索引与包围盒广相, 供各检查取候选对
  4. 统计解析/裁剪质量, 供 :mod:`dfm.core.coverage` 输出数据质量报告
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from dfm.geom.clearance import MAX_CANDIDATE_PAIRS, pair_candidates
from dfm.geom.elements import (ELEMENT_HOLE, ELEMENT_PAD, ELEMENT_POUR,
                               ELEMENT_TRACE, ELEMENT_VIA, Element,
                               POLARITY_CLEAR, POLARITY_DARK)
from dfm.geom.polarity import PolarityStats, compose_all
from dfm.geom.shapes import Shape, to_points

#: 参与间距计算的最大铜箔元素数 (超出则截断并在 stats 标记)
MAX_ELEMENTS = 400_000
#: 最小可判定线宽/尺寸 (低于此值视为解析异常, 不参与判定)
MIN_FEATURE_MM = 1e-6


@dataclass
class GeometryStats:
    """几何构建统计 (进入 analysis["data_quality"])。"""

    trace_count: int = 0
    pad_count: int = 0
    via_count: int = 0
    pour_count: int = 0
    hole_count: int = 0
    clear_count: int = 0
    boundary_cline_count: int = 0
    layers: List[str] = field(default_factory=list)
    bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    truncated: bool = False
    element_cap: int = MAX_ELEMENTS
    etch_width_is_average: bool = False
    shape_stats: Dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "铜箔元素总数": (self.trace_count + self.pad_count + self.via_count
                          + self.pour_count),
            "走线段": self.trace_count,
            "焊盘": self.pad_count,
            "过孔焊盘": self.via_count,
            "铺铜多边形": self.pour_count,
            "钻孔": self.hole_count,
            "层数(有铜)": len(self.layers),
            "元素上限": self.element_cap,
            "是否截断": "是" if self.truncated else "否",
            "走线宽为网络平均值": "是" if self.etch_width_is_average else "否",
        }


class BoardGeometry:
    """共享几何模型。"""

    def __init__(self, design: Any = None, fab_data: Optional[Sequence[dict]] = None,
                 include_holes: bool = True, element_cap: int = MAX_ELEMENTS):
        self.design = design
        self.fab_data = list(fab_data or [])
        self.include_holes = include_holes
        self.element_cap = int(element_cap or MAX_ELEMENTS)
        self.all_elements: List[Element] = []
        self.elements: List[Element] = []
        self.holes: List[Element] = []
        self.by_layer: Dict[str, List[Element]] = {}
        self.stats = GeometryStats(element_cap=self.element_cap)
        self.polarity = PolarityStats()
        self.warnings: List[str] = []
        self.outline: List[Tuple[float, float]] = []
        self.build()

    # ---------------- 构建 ----------------
    def build(self) -> BoardGeometry:
        if self.design is not None:
            self._build_from_design()
        if not self.all_elements and self.fab_data:
            self._build_from_fab_data()
        self._compose_polarity()
        if self.include_holes:
            self._collect_holes()
        self._finalize()
        return self

    def _add(self, el: Element) -> bool:
        if len(self.all_elements) >= self.element_cap:
            self.stats.truncated = True
            return False
        self.all_elements.append(el)
        return True

    def _build_from_design(self) -> None:
        d = self.design
        self.outline = list(getattr(d, "outline", []) or [])
        self.stats.etch_width_is_average = bool(getattr(d, "etch_width_is_average", False))
        copper_order = list(d.copper_layers() or [])

        # ---- 走线 ----
        for cl in getattr(d, "clines", []) or []:
            pts = cl.get("points") or []
            if len(pts) < 2:
                continue
            width = cl.get("width") or 0.0
            polarity = str(cl.get("polarity") or POLARITY_DARK)
            if width <= MIN_FEATURE_MM:
                # 宽度为 0 的折线是 region 边界 (由铺铜多边形表达), 不重复计入铜箔
                self.stats.boundary_cline_count += 1
                continue
            closed = (abs(pts[0][0] - pts[-1][0]) < 1e-7
                      and abs(pts[0][1] - pts[-1][1]) < 1e-7)
            if closed and len(pts) >= 4 and polarity == POLARITY_CLEAR:
                self._add_pour(pts, cl.get("layer", ""), cl.get("net", ""), polarity)
                continue
            for i in range(len(pts) - 1):
                x1, y1 = pts[i]
                x2, y2 = pts[i + 1]
                shape = Shape.segment(x1, y1, x2, y2, width)
                self._add(Element(ELEMENT_TRACE, shape, layer=cl.get("layer", ""),
                                  net=cl.get("net", ""), polarity=polarity,
                                  attrs={"width": width}))
                self._count("trace")

        # ---- 焊盘 (含测试点) ----
        for p in getattr(d, "pads", []) or []:
            if p.get("x") is None:
                continue
            shape = self._pad_shape(p)
            if shape is None:
                continue
            self._add(Element(ELEMENT_PAD, shape, layer=p.get("layer", ""),
                              net=p.get("net", ""), ref=p.get("refdes", ""),
                              pin=p.get("pin", ""),
                              polarity=str(p.get("polarity") or POLARITY_DARK),
                              attrs={"is_smd": bool(p.get("is_smd")),
                                     "pad_w": p.get("pad_w"), "pad_h": p.get("pad_h")}))
            self._count("pad")

        for t in getattr(d, "testpoints", []) or []:
            if t.get("x") is None or not t.get("pad_mm"):
                continue
            shape = Shape.circle(t["x"], t["y"], t["pad_mm"])
            self._add(Element(ELEMENT_PAD, shape, layer=t.get("layer", ""),
                              net=t.get("net", ""), ref=t.get("refdes", ""),
                              pin=t.get("pin", ""),
                              attrs={"testpoint": True, "pad_mm": t["pad_mm"]}))
            self._count("pad")

        # ---- 过孔 ----
        layers = self._via_layers(d, copper_order)
        for v in getattr(d, "vias", []) or []:
            if v.get("x") is None:
                continue
            pad_mm = v.get("pad_mm")
            if pad_mm and pad_mm > 0:
                shape = Shape.circle(v["x"], v["y"], pad_mm)
                for lyr in layers(v):
                    self._add(Element(ELEMENT_VIA, shape, layer=lyr, net=v.get("net", ""),
                                      attrs={"drill_mm": v.get("drill_mm"),
                                             "pad_mm": pad_mm,
                                             "via_type": v.get("type", "")}))
                    self._count("via")

        # ---- 铺铜 ----
        for s in getattr(d, "shapes", []) or []:
            pts = to_points(s.get("points"))
            approx = False
            if len(pts) < 3:
                bb = s.get("bbox") or []
                if len(bb) < 4 or bb[0] is None:
                    continue
                pts = [(bb[0], bb[1]), (bb[2], bb[1]), (bb[2], bb[3]), (bb[0], bb[3])]
                approx = True
                if "铺铜仅有包围盒 (无轮廓点), 按矩形近似" not in self.warnings:
                    self.warnings.append("铺铜仅有包围盒 (无轮廓点), 按矩形近似: "
                                         "参与连通性判定, 但不据此报短路")
            self._add_pour(pts, s.get("layer", ""), s.get("net", ""),
                           str(s.get("polarity") or POLARITY_DARK), approx=approx)

    def _add_pour(self, pts, layer: str, net: str, polarity: str,
                  approx: bool = False) -> None:
        if len(pts) < 3:
            return
        shape = Shape.polygon(pts)
        if shape.is_empty():
            return
        self._add(Element(ELEMENT_POUR, shape, layer=layer, net=net, polarity=polarity,
                          attrs={"vertices": len(pts), "approx": approx}))
        self._count("pour")

    @staticmethod
    def _pad_shape(p: dict) -> Optional[Shape]:
        """按真实形状构造焊盘 (圆/矩形/椭圆/多边形), 缺形状信息时退化为圆。"""
        x, y = p.get("x"), p.get("y")
        if x is None or y is None:
            return None
        kind = str(p.get("pad_shape") or "").lower()
        w = p.get("pad_w") or 0.0
        h = p.get("pad_h") or w or 0.0
        rot = float(p.get("pad_rot") or 0.0)
        pts = to_points(p.get("points"))
        if kind == "poly" and len(pts) >= 3:
            return Shape.polygon(pts)
        if kind in ("rect", "rectangle", "square") and w > 0:
            return Shape.rect(x, y, w, h or w, rot)
        if kind in ("oval", "oblong", "roundrect") and w > 0:
            return Shape.oblong(x, y, w, h or w, rot)
        d = max(w or 0.0, h or 0.0)
        if d <= 0:
            return None
        return Shape.circle(x, y, d)

    @staticmethod
    def _via_layers(d, copper_order: Sequence[str]):
        """返回函数: 过孔 → 需要参与该层铜箔的层名列表。"""
        order = list(copper_order)

        def layers(v: dict) -> List[str]:
            start, end = v.get("start_layer"), v.get("end_layer")
            if start and end and start in order and end in order:
                i, j = order.index(start), order.index(end)
                if i > j:
                    i, j = j, i
                return order[i:j + 1]
            out = [l for l in (start, end) if l]
            if out:
                return out
            return [order[0], order[-1]] if len(order) >= 2 else list(order)

        return layers

    def _count(self, key: str) -> None:
        attr = {"trace": "trace_count", "pad": "pad_count", "via": "via_count",
                "pour": "pour_count"}.get(key)
        if attr:
            setattr(self.stats, attr, getattr(self.stats, attr) + 1)
        shape_key = key
        self.stats.shape_stats[shape_key] = self.stats.shape_stats.get(shape_key, 0) + 1

    # ---------------- 从 fab_data 构建 (无设计模型时的兜底) ----------------
    def _build_from_fab_data(self) -> None:
        for d in self.fab_data:
            kind = d.get("kind")
            layer_default = str(d.get("name") or "")
            if kind in ("gerber", "odbpp"):
                for p in d.get("primitives") or d.get("features") or []:
                    self._add_fab_primitive(p, layer_default)
            elif kind == "drill":
                for h in d.get("holes") or []:
                    self._add_hole(h, layer_default)

    def _add_fab_primitive(self, p: dict, layer_default: str) -> None:
        t = p.get("type")
        layer = str(p.get("layer") or layer_default)
        net = str(p.get("net") or "")
        polarity = POLARITY_CLEAR if p.get("clear") else POLARITY_DARK
        if polarity == POLARITY_CLEAR:
            self.stats.clear_count += 1
        if t in ("draw", "arc"):
            if p.get("x1") is None:
                return
            width = p.get("width") or 0.0
            if width <= MIN_FEATURE_MM:
                self.stats.boundary_cline_count += 1
                return
            if t == "arc":
                shape = Shape.arc(p["x1"], p["y1"], p.get("x2"), p.get("y2"),
                                  (p.get("x1") or 0) + (p.get("i") or 0),
                                  (p.get("y1") or 0) + (p.get("j") or 0),
                                  bool(p.get("cw")), width)
            else:
                shape = Shape.segment(p["x1"], p["y1"], p.get("x2"), p.get("y2"), width)
            self._add(Element(ELEMENT_TRACE, shape, layer=layer, net=net,
                              polarity=polarity, attrs={"width": width}))
            self._count("trace")
        elif t == "flash":
            if p.get("x1") is None:
                return
            shape = self._pad_shape({
                "x": p["x1"], "y": p["y1"], "pad_w": p.get("w") or p.get("width"),
                "pad_h": p.get("h") or p.get("width"), "pad_shape": p.get("shape"),
                "pad_rot": p.get("rot"), "points": p.get("points"),
            }) or Shape.circle(p["x1"], p["y1"], max(p.get("width") or 0.2, 0.01))
            self._add(Element(ELEMENT_PAD, shape, layer=layer, net=net,
                              ref=p.get("refdes", ""), pin=p.get("pin", ""),
                              polarity=polarity))
            self._count("pad")
        elif t == "region":
            pts = to_points(p.get("points"))
            if len(pts) >= 3:
                self._add_pour(pts, layer, net, polarity)

    def _add_hole(self, h: dict, layer_default: str) -> None:
        if h.get("x") is None:
            return
        d = h.get("diameter_mm") or h.get("d") or 0.0
        if d <= 0:
            return
        if h.get("is_slot") and h.get("x2") is not None:
            shape = Shape.segment(h["x"], h["y"], h["x2"], h["y2"], d)
        else:
            shape = Shape.circle(h["x"], h["y"], d)
        el = Element(ELEMENT_HOLE, shape, layer=str(h.get("layer") or "__drill__"),
                     net=str(h.get("net") or ""),
                     attrs={"plating": h.get("plating", ""), "tool": h.get("tool"),
                            "diameter_mm": d, "is_slot": bool(h.get("is_slot"))})
        self.holes.append(el)
        self.stats.hole_count += 1

    # ---------------- 极性合成与收尾 ----------------
    def _compose_polarity(self) -> None:
        self.stats.clear_count += sum(1 for e in self.all_elements
                                      if e.polarity == POLARITY_CLEAR)
        composed = compose_all(self.all_elements, self.polarity)
        self.by_layer = composed
        self.elements = [e for items in composed.values() for e in items]
        self.stats.layers = sorted(composed.keys())
        if self.polarity.clear_elements:
            self.warnings.append(
                f"检测到 {self.polarity.clear_elements} 个负片(clear)图元, 已做极性合成: "
                f"完全挖除 {self.polarity.dropped_elements} 个, "
                f"部分挖除 {self.polarity.clipped_elements} 个")
        if self.stats.truncated:
            self.warnings.append(
                f"铜箔元素超过上限 {self.element_cap}, 已截断 (间距/短路结论可能不完整)")

    def _collect_holes(self) -> None:
        if self.holes:
            return
        d = self.design
        if d is None:
            return
        seen = set()
        for v in getattr(d, "vias", []) or []:
            self._hole_from(v.get("x"), v.get("y"), v.get("drill_mm"), v.get("net", ""), seen)
        for p in getattr(d, "pads", []) or []:
            self._hole_from(p.get("x"), p.get("y"), p.get("drill_mm"), p.get("net", ""), seen)

    def _hole_from(self, x, y, drill, net, seen) -> None:
        if x is None or not drill or drill <= 0:
            return
        key = (round(x, 4), round(y, 4), round(drill, 4))
        if key in seen:
            return
        seen.add(key)
        el = Element(ELEMENT_HOLE, Shape.circle(x, y, drill), layer="__drill__",
                     net=str(net or ""), attrs={"diameter_mm": drill})
        self.holes.append(el)
        self.stats.hole_count += 1

    def _finalize(self) -> None:
        xs0 = ys0 = xs1 = ys1 = None
        for e in self.elements:
            xs0 = e.minx if xs0 is None else min(xs0, e.minx)
            ys0 = e.miny if ys0 is None else min(ys0, e.miny)
            xs1 = e.maxx if xs1 is None else max(xs1, e.maxx)
            ys1 = e.maxy if ys1 is None else max(ys1, e.maxy)
        if None in (xs0, ys0, xs1, ys1) and self.design is not None:
            xs0, ys0, xs1, ys1 = self.design.bbox
        self.stats.bbox = (float(xs0 or 0), float(ys0 or 0), float(xs1 or 0), float(ys1 or 0))

    # ---------------- 查询 ----------------
    def copper(self, layer: Optional[str] = None) -> List[Element]:
        if layer is None:
            return self.elements
        return self.by_layer.get(layer, [])

    def layers(self) -> List[str]:
        return list(self.stats.layers)

    def outer_layers(self) -> List[str]:
        order = self.layers()
        if not order:
            return []
        if self.design is not None:
            cl = list(self.design.copper_layers() or [])
            if len(cl) >= 2:
                return [cl[0], cl[-1]]
        return [order[0], order[-1]]

    def candidates(self, tol: float, filter_fn=None,
                   budget: int = MAX_CANDIDATE_PAIRS) -> Iterator[Tuple[int, int]]:
        """铜箔元素候选对 (索引对, 相对 :attr:`elements`)。"""
        return pair_candidates(self.elements, tol, filter_fn=filter_fn, budget=budget)

    def same_layer_diff_net(self, a: Element, b: Element) -> bool:
        return a.layer == b.layer and a.net != b.net

    def same_layer(self, a: Element, b: Element) -> bool:
        return a.layer == b.layer

    def bbox(self) -> Tuple[float, float, float, float]:
        return self.stats.bbox

    def summary(self) -> dict:
        out = self.stats.as_dict()
        out.update(self.polarity.as_dict())
        return out
