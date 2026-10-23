"""几何制造 DFM 校验 (定位到 XY) — 基于 :mod:`dfm.geom` 真实形状内核。

输入: 归一设计模型 (:class:`dfm.io.brd_model.BrdDesign`) + 制程能力 + 规则集
输出: 问题实例 (每处违规一条, 含 x/y/layer/net/measured/required) 与判定覆盖率

相对旧实现的修复:
  * **真实形状**: 矩形/椭圆/多边形焊盘与铺铜按真实轮廓算净距, 不再外接圆近似
  * **极性合成**: 负片图元 (anti-pad/散热环/内层平面挖空) 已由 BoardGeometry 处理,
    不再产生假短路与假间距违规
  * **计数与实例分离**: 违规处数全量计数, 实例按上限留存并标记 ``truncated``
  * **告警带统一**: "接近制程极限"阈值由 :func:`dfm.core.issue.warn_threshold`
    统一计算, 受规则集 ``limit_warn_ratio`` 控制 (旧实现硬编码 1.2/0.8)
  * **覆盖率可见**: 能力库缺项不再静默跳过, 记入 :class:`dfm.core.coverage.Coverage`
  * **新增规则**: 孔到板边、孔到孔 (不同网络孔壁间距, CAF 风险)、槽孔宽度

校验项 (均可定位 XY):
  线路: 最小线宽 / 最小线距(不同网络铜箔) / 内外层线路到板边
  钻孔: 最小机械孔 / 最大机械孔 / 最小激光孔 / 单边孔环 / 激光孔环 / PTH纵横比 /
        孔到板边 / 孔到孔 / 槽孔宽度
  阻焊: 最小阻焊桥(外层焊盘间)
  测试: 电测/测试点焊盘尺寸
  DRC : Allegro 自身 DRC 违规透传
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dfm.core.capability import (_num, _to_mm, get_line_spacing_capability,
                                 get_sm_bridge_capability)
from dfm.core.coverage import Coverage
from dfm.core.issue import IssueCollector, classify_limit
from dfm.core.registry import REQ_CAPABILITY, REQ_DESIGN, register_check
from dfm.geom.board import BoardGeometry
from dfm.geom.clearance import batch_distances, element_location
from dfm.geom.elements import (ELEMENT_PAD, ELEMENT_TRACE,
                               ELEMENT_VIA, Element)

#: 每条规则最多留存的实例(定位点)数; 处数仍全量计数
MAX_INSTANCES_PER_RULE = 500
#: 净距批量计算的分批大小 (限制内存占用, 同时保持 shapely 向量化收益)
CLEARANCE_BATCH = 200_000


@dataclass
class GeometryResult:
    """几何检查结果。"""

    instances: List[dict] = field(default_factory=list)
    collector: Optional[IssueCollector] = None
    coverage: Optional[Coverage] = None
    stats: Dict[str, Any] = field(default_factory=dict)

    @property
    def issues(self) -> List[dict]:
        return self.collector.aggregate() if self.collector else []


def _classify(measured: float, cap: float, higher_is_better: bool,
              ratio: Optional[float] = None) -> str:
    """分级 (保留旧名称, 内部统一走 :func:`dfm.core.issue.classify_limit`)。"""
    return classify_limit(measured, cap, higher_is_better, ratio)


def _sev_ok(sev: str) -> bool:
    return sev in ("危险", "高")


def _outer_layers(geom: BoardGeometry) -> set:
    return set(geom.outer_layers() or [])


def _check_clearances(els: List[Element], geom: BoardGeometry, tol: float,
                      cap_space: Optional[float], sm_bridge: Optional[float],
                      outer: set, avg_width: bool, ratio: Optional[float],
                      col: IssueCollector, copper_oz: float) -> None:
    """间距 / 阻焊桥检查 — 三段式, 避免为每对候选付 GEOS 代价。

    1. 广相 (包围盒扫描剪枝) → 候选对
    2. **廉价过滤** (同层/不同网络/平均线宽规则) → 需要精算的对
    3. **批量向量化净距** (:func:`dfm.geom.clearance.batch_distances`, shapely C 层循环)
    4. 仅对**确认违规**的对计算定位点 (:func:`element_location`)

    分批处理以限制内存 (每批 :data:`CLEARANCE_BATCH` 对)。
    """
    batch: List[Tuple[int, int]] = []

    def flush() -> None:
        if not batch:
            return
        dists = batch_distances(els, batch)
        for (i, j), dist in zip(batch, dists):
            if dist >= tol:
                continue
            a, b = els[i], els[j]
            # 分类只需距离; 定位点只在真要报告时算
            sev_space = _classify(dist, cap_space, True, ratio) if cap_space else ""
            is_bridge = (sm_bridge is not None and a.layer in outer and b.layer in outer
                         and a.kind in (ELEMENT_PAD, ELEMENT_VIA)
                         and b.kind in (ELEMENT_PAD, ELEMENT_VIA))
            sev_bridge = _classify(dist, sm_bridge, True, ratio) if is_bridge else ""
            if not (_sev_ok(sev_space) or _sev_ok(sev_bridge)):
                continue
            cx, cy = element_location(a, b, dist)
            if _sev_ok(sev_space):
                col.add("线路", "最小线距", sev_space,
                        f"网络 {a.net or '?'} 与 {b.net or '?'} 净距 "
                        f"{round(dist, 4)}mm {'小于' if sev_space == '危险' else '接近'}"
                        f"制程能力 {cap_space}mm (层 {a.layer})",
                        x=cx, y=cy, layer=a.layer, net=a.net,
                        measured=round(dist, 4), required=round(cap_space, 4),
                        unit="mm", from_=a.label(), to=b.label(),
                        suggestion="增大不同网络铜箔间距至制程能力以上")
            if _sev_ok(sev_bridge):
                col.add("阻焊", "最小阻焊桥", sev_bridge,
                        f"{a.label()} 与 {b.label()} 焊盘净距 {round(dist, 4)}mm "
                        f"{'小于' if sev_bridge == '危险' else '接近'}阻焊桥能力 "
                        f"{round(sm_bridge, 4)}mm (层 {a.layer})",
                        x=cx, y=cy, layer=a.layer, net=a.net,
                        measured=round(dist, 4), required=round(sm_bridge, 4),
                        unit="mm", from_=a.label(), to=b.label(),
                        suggestion="加宽阻焊桥，避免焊接锡桥短路",
                        note=f"铜厚 {copper_oz}oz")
        batch.clear()

    for i, j in geom.candidates(tol):
        a, b = els[i], els[j]
        if a.layer != b.layer or a.net == b.net:
            continue
        if avg_width and (a.kind == ELEMENT_TRACE or b.kind == ELEMENT_TRACE):
            # 走线宽为网络平均值时, 间距仅采信焊盘/过孔真实尺寸
            continue
        batch.append((i, j))
        if len(batch) >= CLEARANCE_BATCH:
            flush()
    flush()


# ============================================================
# 主入口
# ============================================================
def analyze_geometry(design, capability: dict, copper_um: float = 35.0,
                     copper_oz: float = 1.0, board_type: str = "rigid",
                     rules=None, geometry: Optional[BoardGeometry] = None,
                     instance_cap: int = MAX_INSTANCES_PER_RULE) -> List[dict]:
    """几何 DFM 校验, 返回问题实例列表 (兼容旧接口)。"""
    return run_geometry_checks(design, capability, copper_um, copper_oz, board_type,
                               rules, geometry, instance_cap).instances


def run_geometry_checks(design, capability: dict, copper_um: float = 35.0,
                        copper_oz: float = 1.0, board_type: str = "rigid",
                        rules=None,
                        geometry: Optional[BoardGeometry] = None,
                        instance_cap: int = MAX_INSTANCES_PER_RULE) -> GeometryResult:
    """几何 DFM 校验 (完整结果: 实例 + 计数 + 覆盖率 + 统计)。

    :param instance_cap: 每规则**留存**的实例(坐标)上限; 处数始终全量计数
    """
    cap = capability or {}
    geom = geometry if geometry is not None else BoardGeometry(design=design)
    col = IssueCollector(cap=int(instance_cap or MAX_INSTANCES_PER_RULE))
    cov = Coverage(check_id="fabrication")
    ratio = float(rules.limit_warn_ratio()) if rules is not None else None

    trace = cap.get("trace", {})
    drill = cap.get("drill", {})
    plating = cap.get("plating", {})
    test = cap.get("test", {})

    line_um, space_um = get_line_spacing_capability(cap, copper_um)
    cap_line = None if line_um is None else line_um / 1000.0
    cap_space = None if space_um is None else space_um / 1000.0
    ann_mech = _to_mm(drill.get("annular_ring_mech"))
    ann_laser = _to_mm(drill.get("annular_ring_laser"))
    min_mech = _num((drill.get("min_mech_drill") or {}).get("value"))
    max_mech = _num((drill.get("max_mech_drill") or {}).get("value"))
    min_laser_um = _num((drill.get("min_laser_via") or {}).get("value"))
    min_laser = None if min_laser_um is None else min_laser_um / 1000.0
    pth_ar = _num((plating.get("pth_aspect_ratio") or {}).get("value"))
    if pth_ar is not None and pth_ar < 2:
        pth_ar = None                       # 能力数据存疑(如 1:1), 不参与判定
    tbe = trace.get("trace_to_board_edge", {})
    edge_inner = _num(tbe.get("inner"))
    edge_outer = _num(tbe.get("outer"))
    sm_bridge = get_sm_bridge_capability(cap, copper_oz)
    test_pad_cap = _num((test.get("min_pad_2wire") or test.get("min_pad") or {}).get("value"))
    npth_edge = _num((drill.get("npth_to_edge") or {}).get("value"))
    h2h = trace.get("hole_to_hole_spacing", {}) or {}
    hole_to_hole = _num((h2h.get("pth_to_pth") or {}).get("value"))
    slot_min = _num((drill.get("plated_slot_min") or {}).get("value"))
    thickness = getattr(design, "thickness_mm", None)

    # 能力缺项 → 记入覆盖率 (不静默跳过)
    for cat, rule, limit in (("线路", "最小线宽", cap_line),
                             ("线路", "最小线距", cap_space),
                             ("线路", "外层线路到板边", edge_outer),
                             ("线路", "内层线路到板边", edge_inner),
                             ("钻孔", "最小机械钻孔", min_mech),
                             ("钻孔", "最大机械钻孔", max_mech),
                             ("钻孔", "最小激光孔", min_laser),
                             ("钻孔", "单边孔环(机械孔)", ann_mech),
                             ("钻孔", "最小激光孔环", ann_laser),
                             ("钻孔", "PTH纵横比", pth_ar),
                             ("钻孔", "NPTH孔到板边", npth_edge),
                             ("钻孔", "孔到孔间距", hole_to_hole),
                             ("钻孔", "槽孔最小宽度", slot_min),
                             ("阻焊", "最小阻焊桥", sm_bridge),
                             ("测试", "电测PAD(二线)", test_pad_cap)):
        if limit is None:
            cov.missed(cat, rule)

    outer = _outer_layers(geom)
    avg_width = bool(getattr(design, "etch_width_is_average", False))

    # ---------- 1. 最小线宽 ----------
    if cap_line is not None:
        cov.judged("线路", "最小线宽", cap_line)
        for el in geom.elements:
            if el.kind != ELEMENT_TRACE:
                continue
            w = float(el.attrs.get("width") or 0.0)
            if w <= 0:
                continue
            sev = _classify(w, cap_line, True, ratio)
            if not _sev_ok(sev):
                continue
            shp = el.shape
            mx = (shp.x + (shp.x2 if shp.x2 is not None else shp.x)) / 2.0
            my = (shp.y + (shp.y2 if shp.y2 is not None else shp.y)) / 2.0
            col.add("线路", "最小线宽", sev,
                    f"网络 {el.net or '?'} 线宽 {round(w, 4)}mm "
                    f"{'低于' if sev == '危险' else '接近制程极限'} {cap_line}mm "
                    f"(层 {el.layer}, 铜厚 {copper_um}um)",
                    x=mx, y=my, layer=el.layer, net=el.net,
                    measured=round(w, 4), required=round(cap_line, 4), unit="mm",
                    suggestion="增大线宽至制程能力以上，或改用更高能力等级/板厂",
                    note=f"铜厚 {copper_um}um")

    # ---------- 2/3. 间距 + 阻焊桥 (真实形状净距, 批量向量化) ----------
    if cap_space is not None or sm_bridge is not None:
        if cap_space is not None:
            cov.judged("线路", "最小线距", cap_space)
        if sm_bridge is not None:
            cov.judged("阻焊", "最小阻焊桥", sm_bridge)
        tol = max(v for v in (cap_space, sm_bridge) if v is not None)
        els = geom.elements
        _check_clearances(els, geom, tol, cap_space, sm_bridge, outer, avg_width,
                          ratio, col, copper_oz)

    # ---------- 4. 钻孔: 孔径 / 孔环 / 纵横比 / 槽孔 ----------
    for cat, rule, limit in (("钻孔", "最小机械钻孔", min_mech),
                             ("钻孔", "最大机械钻孔", max_mech),
                             ("钻孔", "最小激光孔", min_laser),
                             ("钻孔", "单边孔环(机械孔)", ann_mech),
                             ("钻孔", "最小激光孔环", ann_laser),
                             ("钻孔", "PTH纵横比", pth_ar),
                             ("钻孔", "槽孔最小宽度", slot_min)):
        if limit is not None:
            cov.judged(cat, rule, limit)

    for v in getattr(design, "vias", []) or []:
        if v.get("x") is None:
            continue
        d = v.get("drill_mm")
        pad = v.get("pad_mm")
        vtype = str(v.get("type", "")).lower()
        is_laser = (vtype in ("microvia", "laser", "blind")
                    or (min_laser is not None and d is not None and d <= min_laser * 1.05))
        if d is not None:
            if is_laser and min_laser is not None:
                sev = _classify(d, min_laser, True, ratio)
                if _sev_ok(sev):
                    col.add("钻孔", "最小激光孔", sev,
                            f"激光孔 {round(d, 4)}mm {'低于' if sev == '危险' else '接近'}"
                            f"能力 {min_laser}mm (网络 {v.get('net') or '?'})",
                            x=v["x"], y=v["y"], layer=v.get("start_layer", ""),
                            net=v.get("net", ""), measured=round(d, 4),
                            required=min_laser, unit="mm",
                            suggestion="增大激光孔径至制程能力以上")
            elif min_mech is not None:
                sev = _classify(d, min_mech, True, ratio)
                if _sev_ok(sev):
                    col.add("钻孔", "最小机械钻孔", sev,
                            f"机械孔 {round(d, 4)}mm {'低于' if sev == '危险' else '接近'}"
                            f"能力 {min_mech}mm (网络 {v.get('net') or '?'})",
                            x=v["x"], y=v["y"], layer=v.get("start_layer", ""),
                            net=v.get("net", ""), measured=round(d, 4),
                            required=min_mech, unit="mm",
                            suggestion="增大机械钻孔孔径至制程能力以上")
            if max_mech is not None and d > max_mech:
                col.add("钻孔", "最大机械钻孔", "中",
                        f"钻孔 {round(d, 4)}mm 超出最大能力 {max_mech}mm "
                        f"(网络 {v.get('net') or '?'})",
                        x=v["x"], y=v["y"], layer=v.get("start_layer", ""),
                        net=v.get("net", ""), measured=round(d, 4), required=max_mech,
                        unit="mm", suggestion="减小机械钻孔孔径至制程能力以内")
            if v.get("is_slot") and slot_min is not None and d < slot_min:
                col.add("钻孔", "槽孔最小宽度", "高",
                        f"槽孔宽度 {round(d, 4)}mm < 能力 {slot_min}mm "
                        f"(槽长 {v.get('slot_length_mm')}mm)",
                        x=v["x"], y=v["y"], layer=v.get("start_layer", ""),
                        net=v.get("net", ""), measured=round(d, 4), required=slot_min,
                        unit="mm", suggestion="加宽槽孔或改用铣槽成型, 与板厂确认能力")
        if d is not None and pad is not None and pad > d:
            ar = (pad - d) / 2.0
            cap_ar = ann_laser if is_laser else ann_mech
            if cap_ar is not None:
                sev = _classify(ar, cap_ar, True, ratio)
                if _sev_ok(sev):
                    rule = "最小激光孔环" if is_laser else "单边孔环(机械孔)"
                    col.add("钻孔", rule, sev,
                            f"{'激光孔' if is_laser else '机械孔'}单边孔环 "
                            f"{round(ar, 4)}mm {'低于' if sev == '危险' else '接近'}能力 "
                            f"{round(cap_ar, 4)}mm (孔径{round(d, 3)}/焊盘{round(pad, 3)}, "
                            f"网络 {v.get('net') or '?'})",
                            x=v["x"], y=v["y"], layer=v.get("start_layer", ""),
                            net=v.get("net", ""), measured=round(ar, 4),
                            required=round(cap_ar, 4), unit="mm",
                            suggestion="增大单边孔环至制程能力以上")
        if thickness and d and pth_ar and not is_laser and not v.get("is_slot"):
            ar_ratio = thickness / d
            if ar_ratio > pth_ar:
                col.add("钻孔", "PTH纵横比", "高",
                        f"纵横比 {ar_ratio:.2f}:1 (板厚{thickness}mm/孔径{round(d, 3)}mm) "
                        f"超过能力 {pth_ar:.0f}:1 (网络 {v.get('net') or '?'})",
                        x=v["x"], y=v["y"], layer=v.get("start_layer", ""),
                        net=v.get("net", ""), measured=round(ar_ratio, 2),
                        required=pth_ar, unit=":1",
                        suggestion="增大最小孔径或减小板厚，降低纵横比")

    # ---------- 5. 线路/铜箔到板边 (真实轮廓, 面到线距离) ----------
    outline_geom = _outline_geometry(design)
    if outline_geom is not None and (edge_outer is not None or edge_inner is not None):
        if edge_outer is not None:
            cov.judged("线路", "外层线路到板边", edge_outer)
        if edge_inner is not None:
            cov.judged("线路", "内层线路到板边", edge_inner)
        _check_copper_to_edge(geom, outline_geom, outer, edge_inner, edge_outer, col, ratio)
    elif edge_outer is not None or edge_inner is not None:
        cov.missed("线路", "线路到板边", "无板框轮廓层, 无法判定到板边距离")

    # ---------- 6. 孔到板边 / 孔到孔 ----------
    if geom.holes:
        if npth_edge is not None and outline_geom is not None:
            cov.judged("钻孔", "NPTH孔到板边", npth_edge)
            _check_hole_to_edge(geom, outline_geom, npth_edge, col)
        if hole_to_hole is not None:
            cov.judged("钻孔", "孔到孔间距", hole_to_hole)
            _check_hole_to_hole(geom, hole_to_hole, col, ratio)

    # ---------- 7. 测试点焊盘 ----------
    if test_pad_cap is not None:
        cov.judged("测试", "电测PAD(二线)", test_pad_cap)
        for tp in getattr(design, "testpoints", []) or []:
            if tp.get("x") is None or tp.get("pad_mm") is None:
                continue
            sev = _classify(tp["pad_mm"], test_pad_cap, True, ratio)
            if _sev_ok(sev):
                col.add("测试", "电测PAD(二线)", sev,
                        f"测试点 {tp.get('refdes') or tp.get('net')} 焊盘 "
                        f"{round(tp['pad_mm'], 4)}mm {'低于' if sev == '危险' else '接近'}"
                        f"能力 {test_pad_cap}mm",
                        x=tp["x"], y=tp["y"], layer=tp.get("layer", ""),
                        net=tp.get("net", ""), measured=round(tp["pad_mm"], 4),
                        required=test_pad_cap, unit="mm",
                        suggestion="增大电测 PAD 尺寸，保证探针接触")

    # ---------- 8. Allegro DRC 透传 ----------
    for d in getattr(design, "drc", []) or []:
        sev = ("危险" if str(d.get("severity", "")).lower() in ("error", "critical", "危险")
               else "高")
        col.add("DRC", str(d.get("type") or "DRC违规"), sev,
                f"Allegro DRC: {d.get('text') or d.get('type') or '违规'}"
                + (f" (层 {d.get('layer')})" if d.get("layer") else ""),
                x=d.get("x"), y=d.get("y"), layer=d.get("layer", ""),
                suggestion="在 Allegro 中定位并修正该 DRC 违规")

    stats = {
        "铜箔元素": len(geom.elements),
        "钻孔": len(geom.holes),
        "违规处数": col.total(),
        "留存实例": len(col.instances),
        "截断规则": col.truncated_rules(),
        "判定覆盖率": f"{cov.ratio * 100:.0f}%",
    }
    return GeometryResult(instances=col.instances, collector=col, coverage=cov, stats=stats)


# ============================================================
# 到板边 / 孔间距 (真实几何)
# ============================================================
def _outline_geometry(design):
    """板框轮廓 → shapely LinearRing (无轮廓返回 None)。"""
    pts = getattr(design, "outline", None) or []
    if len(pts) < 3:
        return None
    try:
        from shapely.geometry import LinearRing, Polygon

        poly = Polygon([(float(p[0]), float(p[1])) for p in pts])
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty:
            return None
        return poly.exterior if hasattr(poly, "exterior") else LinearRing(pts)
    except Exception:
        return None


def _check_copper_to_edge(geom: BoardGeometry, outline, outer: set,
                          edge_inner, edge_outer, col: IssueCollector,
                          ratio: Optional[float]) -> None:
    from shapely.geometry import Point

    checked = 0
    for el in geom.elements:
        if el.kind == ELEMENT_TRACE and checked >= 20000:
            break
        cap_here = edge_outer if el.layer in outer else edge_inner
        if cap_here is None:
            continue
        checked += 1
        try:
            g = el.geometry
            dist = float(g.distance(outline))
            if g.length and hasattr(g, "centroid"):
                pt = g.representative_point()
            else:
                pt = Point(el.shape.x, el.shape.y)
            px, py = float(pt.x), float(pt.y)
        except Exception:
            continue
        if dist >= cap_here:
            continue
        # 落在板框外的图元 (板框近似/拐角误差) 交由 DRC 判定, 避免误报
        try:
            from shapely.geometry import Polygon

            if not Polygon(outline.coords).contains(Point(px, py)):
                continue
        except Exception:
            pass
        rule = "外层线路到板边" if el.layer in outer else "内层线路到板边"
        col.add("线路", rule, "高",
                f"网络 {el.net or '?'} 铜箔到板边 {round(max(dist, 0.0), 4)}mm "
                f"低于要求 {cap_here}mm (层 {el.layer})",
                x=px, y=py, layer=el.layer, net=el.net,
                measured=round(max(dist, 0.0), 4), required=cap_here, unit="mm",
                suggestion="增大线路到板边距离，避免露铜/破边")


def _check_hole_to_edge(geom: BoardGeometry, outline, limit: float,
                        col: IssueCollector) -> None:
    for h in geom.holes:
        if h.attrs.get("plating", "").upper() not in ("NPTH", ""):
            continue
        try:
            dist = float(h.geometry.distance(outline)) - (h.shape.w or 0.0) / 2.0
        except Exception:
            continue
        dist = max(dist, 0.0)
        if dist >= limit:
            continue
        col.add("钻孔", "NPTH孔到板边", "高",
                f"孔壁到板边 {round(dist, 4)}mm < 要求 {limit}mm (孔径 "
                f"{round(h.shape.w or 0, 3)}mm)，存在破孔风险",
                x=h.shape.x, y=h.shape.y, measured=round(dist, 4), required=limit,
                unit="mm", net=h.net,
                suggestion="增大 NPTH 孔壁到板边距离，或与板厂确认成型公差")


def _check_hole_to_hole(geom: BoardGeometry, limit: float, col: IssueCollector,
                        ratio: Optional[float]) -> None:
    """不同网络孔壁间距 (CAF 风险)。"""
    holes = geom.holes
    if len(holes) < 2:
        return
    for i, j in _hole_candidates(holes, limit):
        a, b = holes[i], holes[j]
        if a.net and b.net and a.net == b.net:
            continue
        try:
            dist = float(a.geometry.distance(b.geometry))
        except Exception:
            continue
        sev = _classify(dist, limit, True, ratio)
        if not _sev_ok(sev):
            continue
        col.add("钻孔", "孔到孔间距", sev,
                f"孔壁间距 {round(dist, 4)}mm {'小于' if sev == '危险' else '接近'}"
                f"能力 {limit}mm (孔径 {round(a.shape.w or 0, 3)}/"
                f"{round(b.shape.w or 0, 3)}mm)",
                x=(a.shape.x + b.shape.x) / 2.0, y=(a.shape.y + b.shape.y) / 2.0,
                measured=round(dist, 4), required=limit, unit="mm",
                net=a.net or b.net, from_=a.net, to=b.net,
                suggestion="增大不同网络孔壁间距，降低 CAF(导电阳极丝)风险")


def _hole_candidates(holes: Sequence[Element], tol: float):
    """孔元素候选对 (包围盒扫描剪枝)。"""
    n = len(holes)
    order = sorted(range(n), key=lambda i: holes[i].minx)
    active: List[int] = []
    count = 0
    for idx in order:
        e = holes[idx]
        xmin = e.minx - tol
        ylo, yhi = e.miny - tol, e.maxy + tol
        if active:
            active = [a for a in active if holes[a].maxx >= xmin]
        for a in active:
            o = holes[a]
            if o.maxy < ylo or o.miny > yhi:
                continue
            yield a, idx
            count += 1
            if count >= 2_000_000:
                return
        active.append(idx)


# ============================================================
# 注册为检查项 (插件式编排入口)
# ============================================================
@register_check(
    id="fabrication",
    name="几何制造 DFM",
    group="制造 DFM",
    description="线宽/线距/孔环/孔径/纵横比/线路与孔到板边/孔到孔/槽孔/阻焊桥/测试点/DRC (定位 XY)",
    requires=(REQ_DESIGN, REQ_CAPABILITY),
    order=20,
)
def run_fabrication(ctx) -> List[dict]:
    """执行几何制造 DFM, 实例与覆盖率写入 ctx.extras 供交叉定位与数据质量报告。"""
    result = run_geometry_checks(ctx.design, ctx.capability, copper_um=ctx.copper_um,
                                 copper_oz=ctx.copper_oz, board_type=ctx.board_type,
                                 rules=ctx.rules, geometry=ctx.geometry,
                                 instance_cap=int(ctx.options.get("instance_cap")
                                                  or MAX_INSTANCES_PER_RULE))
    for it in result.instances:
        it.setdefault("module", "fabrication")
    ctx.extras.setdefault("instances", []).extend(result.instances)
    ctx.extras["geometry_counters"] = dict(result.collector.counts)
    ctx.extras["geometry_stats_checks"] = result.stats
    ctx.set_coverage(result.coverage)
    ctx.note(f"    几何制造 DFM: 违规 {result.collector.total()} 处 "
             f"(留存 {len(result.instances)}), 覆盖率 {result.coverage.ratio * 100:.0f}%")
    for rule in result.collector.truncated_rules():
        ctx.add_truncation(f"几何 DFM 规则 {rule} 违规处数超出留存上限 "
                           f"{result.collector.cap}, 已按真实计数报告")
    return result.issues
