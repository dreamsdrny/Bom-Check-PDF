"""
BRD-AI: 几何 DFM 校验 (定位到 XY)
=============================================================
输入: src.brd_geometry.BrdDesign (SKILL 导出) + 制程能力 capability
输出: DFM 问题实例列表 (每处违规一条, 含 x/y/layer/net/measured/required),
      与 src.dfm_rules.make_issue 结构一致, 可直接:
        - 送入 fab_viewer 交叉定位高亮
        - 由 brd_dfm.aggregate_by_rule 汇总后进 Valor 式报告

校验项 (均可定位 XY):
  线路: 最小线宽 / 最小间距(不同网络铜箔) / 线路到板边
  钻孔: 最小机械孔 / 最小激光孔 / 最大孔 / 单边孔环 / PTH纵横比
  阻焊: 最小阻焊桥(外层焊盘间)
  测试: 电测/测试点焊盘尺寸
  DRC : Allegro 自身 DRC 违规透传 (含XY)

性能: 采用均匀网格空间索引; 对超大板设置实例上限并标注截断。
"""
from typing import Dict, List, Optional, Tuple

from src.brd_geometry import (BrdDesign, sweep_pairs, seg_seg_dist,
                              point_poly_edge_dist, point_in_polygon)
from src.dfm_engine import (get_line_spacing_capability, get_sm_bridge_capability,
                            _num, _to_mm)
from src.dfm_rules import make_issue

MAX_INSTANCES_PER_RULE = 500      # 每类问题最多保留的实例(定位点)数
MAX_ELEMENTS = 400000             # 参与间距计算的铜箔元素上限


class _El:
    """铜箔元素: 统一为 线段(p1->p2) + 半径 r (焊盘/过孔退化为中心点)"""
    __slots__ = ("net", "layer", "x1", "y1", "x2", "y2", "r",
                 "minx", "miny", "maxx", "maxy", "kind", "ref")

    def __init__(self, kind, net, layer, x1, y1, x2, y2, r, ref=""):
        self.kind = kind
        self.net = net or ""
        self.layer = layer or ""
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
        self.r = r or 0.0
        self.minx = min(x1, x2) - self.r
        self.maxx = max(x1, x2) + self.r
        self.miny = min(y1, y2) - self.r
        self.maxy = max(y1, y2) + self.r
        self.ref = ref


def _classify(measured: float, cap: float, higher_is_better: bool) -> str:
    """返回 severity: 危险(超出能力) / 高(接近极限) / '' (合格)"""
    if cap is None or measured is None:
        return ""
    if higher_is_better:
        if measured < cap:
            return "危险"
        if measured < cap * 1.2:
            return "高"
    else:
        if measured > cap:
            return "危险"
        if measured > cap * 0.8:
            return "高"
    return ""


def _sev_ok(sev: str) -> bool:
    return sev in ("危险", "高")


def _outer_layers(design: BrdDesign) -> set:
    layers = design.copper_layers()
    if not layers:
        return set()
    return {layers[0], layers[-1]}


def _build_elements(design: BrdDesign) -> List[_El]:
    els: List[_El] = []
    for cl in design.clines:
        pts = cl["points"]
        w = cl["width"] or 0.0
        r = w / 2.0
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            els.append(_El("seg", cl["net"], cl["layer"], x1, y1, x2, y2, r,
                           ref=f"cline:{cl['net']}"))
            if len(els) >= MAX_ELEMENTS:
                return els
    for p in design.pads:
        if p["x"] is None:
            continue
        w = p["pad_w"] or p["pad_h"] or 0.3
        r = w / 2.0
        els.append(_El("pad", p["net"], p["layer"], p["x"], p["y"], p["x"], p["y"], r,
                       ref=f"{p['refdes']}.{p['pin']}"))
        if len(els) >= MAX_ELEMENTS:
            return els
    for v in design.vias:
        if v["x"] is None:
            continue
        r = (v["pad_mm"] or 0.5) / 2.0
        for lyr in {v["start_layer"], v["end_layer"]}:
            if not lyr:
                continue
            els.append(_El("via", v["net"], lyr, v["x"], v["y"], v["x"], v["y"], r,
                           ref=f"via:{v['net']}"))
            if len(els) >= MAX_ELEMENTS:
                return els
    return els


def analyze_geometry(design: BrdDesign, capability: dict,
                     copper_um: float = 35.0, copper_oz: float = 1.0,
                     board_type: str = "rigid") -> List[dict]:
    """几何 DFM 校验, 返回问题实例列表 (每处一条, 含 XY)"""
    issues: List[dict] = []
    trace = capability.get("trace", {})
    drill = capability.get("drill", {})
    plating = capability.get("plating", {})
    sm = capability.get("solder_mask", {})
    test = capability.get("test", {})

    line_um, space_um = get_line_spacing_capability(capability, copper_um)
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
        pth_ar = None
    tbe = trace.get("trace_to_board_edge", {})
    edge_inner = _num(tbe.get("inner"))
    edge_outer = _num(tbe.get("outer"))
    sm_bridge = get_sm_bridge_capability(capability, copper_oz)
    test_pad_cap = _num((test.get("min_pad_2wire") or test.get("min_pad") or {}).get("value"))

    counters: Dict[str, int] = {}

    def emit(category, rule, severity, description, x=None, y=None, layer="",
             net="", measured=None, required=None, unit="mm", suggestion="",
             from_="", to="", note=""):
        key = f"{category}|{rule}"
        if counters.get(key, 0) >= MAX_INSTANCES_PER_RULE:
            return
        counters[key] = counters.get(key, 0) + 1
        prefix = {"线路": "FAB-TR", "钻孔": "FAB-DR", "阻焊": "FAB-SM",
                  "测试": "FAB-TS", "制造数据": "FAB", "DRC": "DRC"}.get(category, "DFM")
        issues.append(make_issue(
            code=f"{prefix}-{counters[key]:04d}", category=category, rule=rule,
            severity=severity, description=description, measured=measured,
            required=required, unit=unit, layer=layer, x=x, y=y,
            suggestion=suggestion, from_=from_, to=to, note=note, net=net,
        ))

    outer = _outer_layers(design)

    # ---------- 1. 最小线宽 ----------
    if cap_line is not None:
        for cl in design.clines:
            w = cl["width"]
            if w is None:
                continue
            sev = _classify(w, cap_line, higher_is_better=True)
            if not _sev_ok(sev):
                continue
            pts = cl["points"]
            if len(pts) >= 2:
                mx = (pts[0][0] + pts[-1][0]) / 2.0
                my = (pts[0][1] + pts[-1][1]) / 2.0
            else:
                continue
            verb = "低于" if sev == "危险" else "接近制程极限"
            emit("线路", "最小线宽", sev,
                 f"网络 {cl['net'] or '?'} 线宽 {round(w,4)}mm {verb} {cap_line}mm "
                 f"(层 {cl['layer']}, 铜厚 {copper_um}um)",
                 x=mx, y=my, layer=cl["layer"], net=cl["net"],
                 measured=round(w, 4), required=round(cap_line, 4),
                 suggestion="增大线宽至制程能力以上，或改用更高能力等级/板厂",
                 note=f"铜厚 {copper_um}um")

    # ---------- 2/3. 间距 + 阻焊桥 (扫描剪枝广相) ----------
    els = _build_elements(design)
    tol = cap_space if cap_space is not None else 0.2
    avg_w = design.etch_width_is_average   # 走线宽为网络平均值时, 间距/短路仅用焊盘/过孔真实尺寸
    space_reported = 0
    bridge_reported = 0
    for i, j in sweep_pairs(els, tol=tol):
        e = els[i]
        o = els[j]
        if e.layer != o.layer or e.net == o.net:
            continue
        if avg_w and (e.kind == "seg" or o.kind == "seg"):
            continue
        d, cx, cy = seg_seg_dist((e.x1, e.y1), (e.x2, e.y2),
                                 (o.x1, o.y1), (o.x2, o.y2))
        clearance = d - e.r - o.r
        if clearance < 0:
            clearance = 0.0
        if clearance > tol:
            continue
        # 2. 最小间距
        if cap_space is not None and space_reported < MAX_INSTANCES_PER_RULE:
            sev = _classify(clearance, cap_space, higher_is_better=True)
            if _sev_ok(sev):
                space_reported += 1
                verb = "小于" if sev == "危险" else "接近"
                emit("线路", "最小线距", sev,
                     f"网络 {e.net or '?'} 与 {o.net or '?'} 间距 {round(clearance,4)}mm "
                     f"{verb}制程能力 {cap_space}mm (层 {e.layer})",
                     x=cx, y=cy, layer=e.layer, net=e.net,
                     measured=round(clearance, 4), required=round(cap_space, 4),
                     from_=e.net or e.ref, to=o.net or o.ref,
                     suggestion="增大不同网络铜箔间距至制程能力以上")
        # 3. 阻焊桥 (仅外层, 焊盘/过孔之间)
        if (sm_bridge is not None and e.layer in outer and o.layer in outer
                and e.kind in ("pad", "via") and o.kind in ("pad", "via")
                and bridge_reported < MAX_INSTANCES_PER_RULE):
            sev = _classify(clearance, sm_bridge, higher_is_better=True)
            if _sev_ok(sev):
                bridge_reported += 1
                verb = "小于" if sev == "危险" else "接近"
                emit("阻焊", "最小阻焊桥", sev,
                     f"{e.ref} 与 {o.ref} 焊盘间距 {round(clearance,4)}mm "
                     f"{verb}阻焊桥能力 {round(sm_bridge,4)}mm (层 {e.layer})",
                     x=cx, y=cy, layer=e.layer, net=e.net,
                     measured=round(clearance, 4), required=round(sm_bridge, 4),
                     from_=e.ref, to=o.ref,
                     suggestion="加宽阻焊桥，避免焊接锡桥短路",
                     note=f"铜厚 {copper_oz}oz")

    # ---------- 4. 钻孔: 孔径 / 孔环 / 纵横比 ----------
    thickness = design.thickness_mm
    for v in design.vias:
        if v["x"] is None:
            continue
        d = v["drill_mm"]
        pad = v["pad_mm"]
        is_laser = (v["type"] in ("microvia", "laser", "blind")
                    or (min_laser is not None and d is not None and d <= min_laser * 1.05))
        # 孔径
        if d is not None:
            if is_laser and min_laser is not None:
                sev = _classify(d, min_laser, higher_is_better=True)
                if _sev_ok(sev):
                    emit("钻孔", "最小激光孔", sev,
                         f"激光孔 {round(d,4)}mm {'低于' if sev=='危险' else '接近'}能力 "
                         f"{min_laser}mm (网络 {v['net'] or '?'})",
                         x=v["x"], y=v["y"], layer=v["start_layer"], net=v["net"],
                         measured=round(d, 4), required=min_laser,
                         suggestion="增大激光孔径至制程能力以上")
            elif min_mech is not None:
                sev = _classify(d, min_mech, higher_is_better=True)
                if _sev_ok(sev):
                    emit("钻孔", "最小机械钻孔", sev,
                         f"机械孔 {round(d,4)}mm {'低于' if sev=='危险' else '接近'}能力 "
                         f"{min_mech}mm (网络 {v['net'] or '?'})",
                         x=v["x"], y=v["y"], layer=v["start_layer"], net=v["net"],
                         measured=round(d, 4), required=min_mech,
                         suggestion="增大机械钻孔孔径至制程能力以上")
            if max_mech is not None and d > max_mech:
                emit("钻孔", "最大机械钻孔", "中",
                     f"钻孔 {round(d,4)}mm 超出最大能力 {max_mech}mm (网络 {v['net'] or '?'})",
                     x=v["x"], y=v["y"], layer=v["start_layer"], net=v["net"],
                     measured=round(d, 4), required=max_mech,
                     suggestion="减小机械钻孔孔径至制程能力以内")
        # 单边孔环
        if d is not None and pad is not None and pad > d:
            ar = (pad - d) / 2.0
            cap_ar = ann_laser if is_laser else ann_mech
            if cap_ar is not None:
                sev = _classify(ar, cap_ar, higher_is_better=True)
                if _sev_ok(sev):
                    rule = "最小激光孔环" if is_laser else "单边孔环(机械孔)"
                    emit("钻孔", rule, sev,
                         f"{'激光孔' if is_laser else '机械孔'}单边孔环 {round(ar,4)}mm "
                         f"{'低于' if sev=='危险' else '接近'}能力 {round(cap_ar,4)}mm "
                         f"(孔径{round(d,3)}/焊盘{round(pad,3)}, 网络 {v['net'] or '?'})",
                         x=v["x"], y=v["y"], layer=v["start_layer"], net=v["net"],
                         measured=round(ar, 4), required=round(cap_ar, 4),
                         suggestion="增大单边孔环至制程能力以上")
        # 纵横比
        if thickness and d and pth_ar and not is_laser:
            ratio = thickness / d
            if ratio > pth_ar:
                emit("钻孔", "PTH纵横比", "高",
                     f"纵横比 {ratio:.2f}:1 (板厚{thickness}mm/孔径{round(d,3)}mm) "
                     f"超过能力 {pth_ar:.0f}:1 (网络 {v['net'] or '?'})",
                     x=v["x"], y=v["y"], layer=v["start_layer"], net=v["net"],
                     measured=round(ratio, 2), required=pth_ar, unit=":1",
                     suggestion="增大最小孔径或减小板厚，降低纵横比")

    # ---------- 5. 线路/铜箔到板边 ----------
    poly = design.outline
    edge_cap = edge_outer if edge_outer is not None else edge_inner
    if edge_cap is not None and (len(poly) >= 3 or design.bbox[2] > design.bbox[0]):
        if len(poly) < 3:
            mnx, mny, mxx, mxy = design.bbox
            poly = [(mnx, mny), (mxx, mny), (mxx, mxy), (mnx, mxy)]
        checked_pts = 0
        for e in els:
            if e.kind == "seg":
                continue
            if checked_pts >= 20000:
                break
            checked_pts += 1
            px = (e.x1 + e.x2) / 2.0
            py = (e.y1 + e.y2) / 2.0
            dist, _cx, _cy = point_poly_edge_dist(px, py, poly)
            dist_to_copper = dist - e.r
            cap_here = edge_outer if e.layer in outer else edge_inner
            if cap_here is None:
                continue
            if dist_to_copper < cap_here:
                inside = point_in_polygon(px, py, poly)
                if not inside:
                    # 板框含圆弧, 提取按弦线近似; 落在近似多边形外的点多为拐角误差,
                    # 真实越界/破边交由 Allegro DRC 判定, 此处跳过以避免误报。
                    continue
                emit("线路", "外层线路到板边" if e.layer in outer else "内层线路到板边",
                     "高",
                     f"网络 {e.net or '?'} 铜箔到板边 {round(max(dist_to_copper,0),4)}mm "
                     f"低于要求 {cap_here}mm (层 {e.layer})",
                     x=px, y=py, layer=e.layer, net=e.net,
                     measured=round(max(dist_to_copper, 0), 4), required=cap_here,
                     suggestion="增大线路到板边距离，避免露铜/破边")

    # ---------- 6. 测试点焊盘 ----------
    if test_pad_cap is not None:
        for tp in design.testpoints:
            if tp["x"] is None or tp["pad_mm"] is None:
                continue
            sev = _classify(tp["pad_mm"], test_pad_cap, higher_is_better=True)
            if _sev_ok(sev):
                emit("测试", "电测PAD(二线)", sev,
                     f"测试点 {tp['refdes'] or tp['net']} 焊盘 {round(tp['pad_mm'],4)}mm "
                     f"{'低于' if sev=='危险' else '接近'}能力 {test_pad_cap}mm",
                     x=tp["x"], y=tp["y"], layer=tp["layer"], net=tp["net"],
                     measured=round(tp["pad_mm"], 4), required=test_pad_cap,
                     suggestion="增大电测 PAD 尺寸，保证探针接触")

    # ---------- 7. Allegro DRC 透传 ----------
    for d in design.drc:
        sev = "危险" if str(d["severity"]).lower() in ("error", "critical", "危险") else "高"
        emit("DRC", d["type"] or "DRC违规", sev,
             f"Allegro DRC: {d['text'] or d['type'] or '违规'}"
             + (f" (层 {d['layer']})" if d["layer"] else ""),
             x=d["x"], y=d["y"], layer=d["layer"],
             suggestion="在 Allegro 中定位并修正该 DRC 违规")

    return issues
