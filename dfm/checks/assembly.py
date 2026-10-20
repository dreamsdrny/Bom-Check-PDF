"""装配 / SMT DFM 检查 (几何量测 + 参数化)。

几何量测项 (均可定位 XY, 支持版图交叉定位):
  * 器件间距        器件本体(焊盘包围盒近似)最小间距
  * 器件到板边      器件本体到成型线距离 (传送/夹持/撞件风险)
  * 焊盘到板边      SMD 焊盘到成型线距离 (无法焊接/工艺边不足)
  * 片式焊盘对称性  两端焊盘尺寸差异 (墓碑效应风险)
  * BGA/细间距      引脚间距、单边孔环
  * Via in Pad      过孔落在 SMD 焊盘内 (需树脂塞孔+电镀盖帽)
  * 丝印上焊盘      丝印图形与焊盘重叠 (需 Gerber 丝印层)
  * 测试点覆盖      网络测试点覆盖率、测试点到板边、测试点尺寸
  * 基准点          每面 Fiducial 数量与尺寸

参数化项 (设计参数或人工填写) 复用 :func:`dfm.core.issue.build_assembly_issues`:
极性标识、钢网厚度、器件数量统计等。

阈值全部来自规则集 ``assembly`` 段 (rules/checks.yaml), 可按客户标准收紧。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from dfm.core.issue import aggregate_by_rule, build_assembly_issues, make_issue
from dfm.core.registry import REQ_DESIGN, register_check
from dfm.io.brd_model import SpatialGrid, point_poly_edge_dist

MAX_INSTANCES = 300
FIDUCIAL_NAMES = ("FID", "MARK", "FP", "BENCH", "基准")


# ============================================================
# 几何工具
# ============================================================
def component_boxes(design) -> List[Dict[str, Any]]:
    """按 refdes 汇总焊盘包围盒作为器件本体近似 (按 cx 排序便于邻近搜索)。"""
    acc: Dict[str, List[float]] = {}
    side: Dict[str, str] = {}
    for p in design.pads:
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
        if ref not in side and p.get("layer"):
            side[ref] = str(p["layer"])
    out = []
    for ref, (x0, y0, x1, y1, npad) in acc.items():
        if npad < 1:
            continue
        out.append({"refdes": ref, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                    "cx": (x0 + x1) / 2.0, "cy": (y0 + y1) / 2.0,
                    "pins": int(npad), "layer": side.get(ref, "")})
    out.sort(key=lambda b: b["cx"])
    return out


def _box_gap(a: dict, b: dict) -> Tuple[float, float, float]:
    """两包围盒间距与最近点坐标"""
    dx = max(0.0, max(a["x0"], b["x0"]) - min(a["x1"], b["x1"]))
    dy = max(0.0, max(a["y0"], b["y0"]) - min(a["y1"], b["y1"]))
    cx = (min(a["x1"], b["x1"]) + max(a["x0"], b["x0"])) / 2.0
    cy = (min(a["y1"], b["y1"]) + max(a["y0"], b["y0"])) / 2.0
    return math.hypot(dx, dy), cx, cy


def _component_side(design, refdes: str) -> str:
    for c in design.components or []:
        if str(c.get("refdes", "")) == refdes:
            return str(c.get("side", "")).lower()
    return ""


def _pads_by_refdes(design) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    for p in design.pads:
        if p.get("refdes") and p.get("x") is not None:
            out.setdefault(p["refdes"], []).append(p)
    return out


# ============================================================
# 各检查实现 (返回问题实例)
# ============================================================
def check_component_spacing(design, min_gap: float, severity: str = "中") -> List[dict]:
    """器件本体最小间距"""
    boxes = component_boxes(design)
    if len(boxes) < 2 or not min_gap:
        return []
    grid = SpatialGrid(cell=max(min_gap * 4.0, 1.0))
    for i, b in enumerate(boxes):
        grid.insert(b["x0"] - min_gap, b["y0"] - min_gap,
                    b["x1"] + min_gap, b["y1"] + min_gap, i)
    out: List[dict] = []
    seen = set()
    for i, b in enumerate(boxes):
        for j in grid.query(b["x0"] - min_gap, b["y0"] - min_gap,
                            b["x1"] + min_gap, b["y1"] + min_gap):
            if j <= i or (i, j) in seen:
                continue
            seen.add((i, j))
            o = boxes[j]
            d, cx, cy = _box_gap(b, o)
            if d >= min_gap:
                continue
            out.append(make_issue(
                code="ASM-SP", category="组装", rule="器件间距", severity=severity,
                description=(f"{b['refdes']} 与 {o['refdes']} 本体间距 {round(d, 3)}mm "
                             f"< 要求 {min_gap}mm，贴片易碰撞/无法返修"),
                measured=round(d, 4), required=min_gap, unit="mm",
                x=cx, y=cy, layer=b.get("layer", ""),
                from_=b["refdes"], to=o["refdes"],
                suggestion="增大器件间距，或改用更小封装；返修空间不足时需工艺评估"))
            if len(out) >= MAX_INSTANCES:
                return out
    return out


def check_component_to_edge(design, min_dist: float, severity: str = "中") -> List[dict]:
    """器件本体到成型线距离"""
    poly = design.outline or []
    if len(poly) < 3 or not min_dist:
        return []
    out: List[dict] = []
    for b in component_boxes(design):
        d = min(point_poly_edge_dist(x, y, poly)[0]
                for x, y in ((b["x0"], b["y0"]), (b["x1"], b["y1"]),
                             (b["x0"], b["y1"]), (b["x1"], b["y0"])))
        if d >= min_dist:
            continue
        out.append(make_issue(
            code="ASM-ED", category="组装", rule="器件到板边", severity=severity,
            description=(f"{b['refdes']} 本体到板边 {round(d, 3)}mm < 要求 {min_dist}mm，"
                         f"传送/夹持易撞件"),
            measured=round(d, 4), required=min_dist, unit="mm",
            x=b["cx"], y=b["cy"], layer=b.get("layer", ""), net="",
            suggestion="内移器件或增加工艺边；无法内移时需与 SMT 厂确认夹持方式"))
        if len(out) >= MAX_INSTANCES:
            break
    return out


def check_pad_to_edge(design, min_dist: float) -> List[dict]:
    """SMD 焊盘到成型线距离 (焊接与成型公差)"""
    poly = design.outline or []
    if len(poly) < 3 or not min_dist:
        return []
    out: List[dict] = []
    for p in design.pads:
        if not p.get("is_smd") or p.get("x") is None:
            continue
        d, _cx, _cy = point_poly_edge_dist(p["x"], p["y"], poly)
        d -= (max(p.get("pad_w") or 0.0, p.get("pad_h") or 0.0) / 2.0)
        if d >= min_dist:
            continue
        out.append(make_issue(
            code="ASM-PE", category="组装", rule="焊盘到板边", severity="中",
            description=(f"{p.get('refdes') or ''}.{p.get('pin') or ''} SMD 焊盘到板边 "
                         f"{round(max(d, 0.0), 3)}mm < 要求 {min_dist}mm"),
            measured=round(max(d, 0.0), 4), required=min_dist, unit="mm",
            x=p["x"], y=p["y"], layer=p.get("layer", ""), net=p.get("net", ""),
            suggestion="焊盘内移或增加工艺边，避免成型后焊盘破损/无法焊接"))
        if len(out) >= MAX_INSTANCES:
            break
    return out


def check_chip_pad_symmetry(design, ratio_min: float) -> List[dict]:
    """两端片式元件焊盘对称性 (墓碑效应)"""
    if not ratio_min:
        return []
    out: List[dict] = []
    for ref, pads in _pads_by_refdes(design).items():
        if len(pads) != 2:
            continue
        a, b = pads
        sa = (a.get("pad_w") or 0) * (a.get("pad_h") or 0)
        sb = (b.get("pad_w") or 0) * (b.get("pad_h") or 0)
        if sa <= 0 or sb <= 0:
            continue
        ratio = min(sa, sb) / max(sa, sb)
        if ratio >= ratio_min:
            continue
        out.append(make_issue(
            code="ASM-SY", category="组装", rule="片式元件焊盘对称性", severity="中",
            description=(f"{ref} 两端焊盘面积对称比 {round(ratio, 3)} < {ratio_min}，"
                         f"回流焊存在墓碑效应风险"),
            measured=round(ratio, 4), required=ratio_min, unit="ratio",
            x=a["x"], y=a["y"], layer=a.get("layer", ""), net=a.get("net", ""),
            from_=f"{ref}.1", to=f"{ref}.2",
            suggestion="平衡两端焊盘尺寸与散热铜箔，必要时缩短焊盘长度或做热隔离"))
        if len(out) >= MAX_INSTANCES:
            break
    return out


def check_fine_pitch(design, pitch_min: float, annular_min: float) -> List[dict]:
    """细间距器件 (BGA/QFN) 引脚间距与单边孔环"""
    out: List[dict] = []
    for ref, pads in _pads_by_refdes(design).items():
        if len(pads) < 4:
            continue
        pts = [(p["x"], p["y"]) for p in pads if p.get("x") is not None]
        if len(pts) < 4:
            continue
        pitch = _min_pitch(pts)
        if pitch is None or not pitch_min or pitch >= pitch_min:
            continue
        drill = [p for p in pads if p.get("drill_mm")]
        ring = None
        if drill and annular_min:
            rings = [((p.get("pad_w") or 0) - p["drill_mm"]) / 2.0
                     for p in drill if p.get("pad_w")]
            ring = min(rings) if rings else None
        sev = "中" if ring is None or ring >= (annular_min or 0) else "高"
        out.append(make_issue(
            code="ASM-FP", category="组装", rule="BGA焊盘", severity=sev,
            description=(f"{ref} 引脚间距 {round(pitch, 3)}mm < {pitch_min}mm (共 {len(pads)} 脚)"
                         + (f"，单边孔环 {round(ring, 4)}mm < {annular_min}mm"
                            if ring is not None and annular_min and ring < annular_min else "")
                         + "，需专项制造符合性分析"),
            measured=round(pitch, 4), required=pitch_min, unit="mm",
            x=pads[0]["x"], y=pads[0]["y"], layer=pads[0].get("layer", ""),
            suggestion="确认板厂细间距能力(线宽/线距/孔环/塞孔)，必要时改扇出方式或更换封装"))
        if len(out) >= MAX_INSTANCES:
            break
    return out


def _min_pitch(pts: List[Tuple[float, float]]) -> Optional[float]:
    """最小相邻焊盘中心距 (X 方向排序 + 剪枝扫描, O(n log n))"""
    n = len(pts)
    if n < 2:
        return None
    order = sorted(range(n), key=lambda i: pts[i][0])
    best = math.inf
    for a, b in zip(order, order[1:]):
        d = math.hypot(pts[b][0] - pts[a][0], pts[b][1] - pts[a][1])
        if 0 < d < best:
            best = d
    for idx, i in enumerate(order):
        x0, y0 = pts[i]
        for j in order[idx + 1:]:
            x1, y1 = pts[j]
            if x1 - x0 > best:
                break
            d = math.hypot(x1 - x0, y1 - y0)
            if 0 < d < best:
                best = d
    return None if best == math.inf else best


def check_via_in_pad(design, allowed: bool = False) -> List[dict]:
    """过孔落在 SMD 焊盘内 (需树脂塞孔 + 电镀盖帽, 否则锡膏流失/空洞)"""
    smd = [p for p in design.pads if p.get("is_smd") and p.get("x") is not None]
    if not smd or not design.vias:
        return []
    grid = SpatialGrid(cell=1.0)
    for i, p in enumerate(smd):
        w = (p.get("pad_w") or 0.3) / 2.0
        h = (p.get("pad_h") or 0.3) / 2.0
        grid.insert(p["x"] - w, p["y"] - h, p["x"] + w, p["y"] + h, i)
    out: List[dict] = []
    for v in design.vias:
        if v.get("x") is None:
            continue
        for i in grid.query(v["x"], v["y"], v["x"], v["y"]):
            p = smd[i]
            w = (p.get("pad_w") or 0.3) / 2.0
            h = (p.get("pad_h") or 0.3) / 2.0
            if abs(v["x"] - p["x"]) <= w and abs(v["y"] - p["y"]) <= h:
                if allowed:
                    sev, msg = "低", "过孔位于焊盘内 (已允许, 建议确认塞孔工艺)"
                else:
                    sev, msg = "高", "过孔位于 SMD 焊盘内 (Via in Pad)"
                out.append(make_issue(
                    code="ASM-VP", category="组装", rule="Via in Pad", severity=sev,
                    description=(f"{msg}: {p.get('refdes') or ''}.{p.get('pin') or ''} "
                                 f"焊盘内存在过孔 (孔径 {round(v.get('drill_mm') or 0, 3)}mm)，"
                                 f"回流焊锡膏流失会导致虚焊/空洞"),
                    measured=v.get("drill_mm"), required=0, unit="mm",
                    x=v["x"], y=v["y"], layer=p.get("layer", ""),
                    net=v.get("net", "") or p.get("net", ""),
                    suggestion="过孔外移(狗骨/扇出)，或采用树脂塞孔+电镀盖帽(VIPPO)工艺并与板厂确认"))
                break
        if len(out) >= MAX_INSTANCES:
            break
    return out


def check_silk_on_pad(design, fab_data: List[dict]) -> List[dict]:
    """丝印上焊盘 (需要 Gerber/ODB 丝印层)"""
    silk = []
    for d in fab_data or []:
        name = str(d.get("name", "")).lower()
        if d.get("kind") not in ("gerber", "odbpp"):
            continue
        if not any(k in name for k in ("silk", "legend", "gto", "gbo", "sst", "ssb", "overlay")):
            continue
        silk.extend(d.get("primitives") or d.get("features") or [])
    if not silk:
        return []
    smd = [p for p in design.pads if p.get("is_smd") and p.get("x") is not None]
    if not smd:
        return []
    grid = SpatialGrid(cell=1.0)
    for i, p in enumerate(smd):
        w = (p.get("pad_w") or 0.3) / 2.0
        h = (p.get("pad_h") or 0.3) / 2.0
        grid.insert(p["x"] - w, p["y"] - h, p["x"] + w, p["y"] + h, i)
    out: List[dict] = []
    hit = set()
    for s in silk:
        pts: List[Tuple[float, float]] = []
        if s.get("type") == "region":
            pts = [(q[0], q[1]) for q in (s.get("points") or [])]
        elif s.get("x1") is not None:
            pts = [(s["x1"], s["y1"])]
            if s.get("x2") is not None:
                pts.append((s["x2"], s["y2"]))
        for x, y in pts:
            for i in grid.query(x, y, x, y):
                if i in hit:
                    continue
                p = smd[i]
                w = (p.get("pad_w") or 0.3) / 2.0
                h = (p.get("pad_h") or 0.3) / 2.0
                if abs(x - p["x"]) <= w and abs(y - p["y"]) <= h:
                    hit.add(i)
                    out.append(make_issue(
                        code="ASM-SK", category="丝印", rule="丝印上焊盘", severity="中",
                        description=(f"丝印图形落在 {p.get('refdes') or ''}."
                                     f"{p.get('pin') or ''} 焊盘上，影响焊接与锡膏印刷"),
                        measured=0, required=0, unit="mm",
                        x=p["x"], y=p["y"], layer=p.get("layer", ""),
                        net=p.get("net", ""),
                        suggestion="丝印移出焊盘区域，或缩小字符/调整位号位置"))
                    break
        if len(out) >= MAX_INSTANCES:
            break
    return out


def check_fiducials(design, min_count: int, min_diameter: float) -> List[dict]:
    """基准点 (Fiducial) 数量与尺寸"""
    out: List[dict] = []
    top = bot = 0
    found: List[dict] = []
    for c in design.components or []:
        ref = str(c.get("refdes", "")).upper()
        if not any(k in ref for k in FIDUCIAL_NAMES):
            continue
        found.append(c)
        if str(c.get("side", "")).lower() in ("bottom", "bot", "b"):
            bot += 1
        else:
            top += 1
    if not found:
        for side_label, cnt in (("A面", top), ("B面", bot)):
            pass
        out.append(make_issue(
            code="ASM-FD", category="组装", rule="基准点数量", severity="高",
            description=f"未识别到全局基准点 (Fiducial)，贴片对位精度无法保证",
            measured=0, required=min_count, unit="个",
            suggestion="每面至少布置 2-3 个全局基准点(直径≥1mm，周边 5mm 内无铜/无器件)"))
        return out
    for side_label, cnt in (("A面", top), ("B面", bot)):
        if cnt and cnt < min_count:
            out.append(make_issue(
                code="ASM-FD", category="组装", rule="基准点数量", severity="高",
                description=f"{side_label}全局基准点 {cnt} 个 < 要求 {min_count} 个",
                measured=cnt, required=min_count, unit="个",
                x=found[0].get("x"), y=found[0].get("y"),
                suggestion=f"{side_label}补足基准点至 {min_count} 个，并保证对角布置"))
    if min_diameter:
        for c in found:
            d = _fiducial_diameter(design, str(c.get("refdes", "")))
            if d is not None and d < min_diameter:
                out.append(make_issue(
                    code="ASM-FD", category="组装", rule="基准点尺寸", severity="中",
                    description=(f"基准点 {c.get('refdes')} 直径 {round(d, 3)}mm < "
                                 f"{min_diameter}mm，视觉识别可靠性下降"),
                    measured=round(d, 4), required=min_diameter, unit="mm",
                    x=c.get("x"), y=c.get("y"),
                    suggestion=f"基准点直径建议 ≥{min_diameter}mm，且周围 2 倍直径内无阻焊/铜箔"))
    return out


def _fiducial_diameter(design, refdes: str) -> Optional[float]:
    sizes = []
    for p in design.pads:
        if str(p.get("refdes", "")) == refdes:
            w = p.get("pad_w") or p.get("pad_h")
            if w:
                sizes.append(w)
    return min(sizes) if sizes else None


def check_test_points(design, coverage_min: float, pad_min: float,
                      edge_min: float) -> List[dict]:
    """测试点覆盖率 / 尺寸 / 到板边"""
    out: List[dict] = []
    tps = [t for t in (design.testpoints or []) if t.get("x") is not None]
    nets = {n: info for n, info in design.nets.items() if len(info.get("nodes") or []) >= 2}
    if nets:
        tested = {str(t.get("net", "")) for t in tps if t.get("net")}
        rate = len(tested & set(nets)) / max(len(nets), 1)
        if coverage_min and rate < coverage_min:
            out.append(make_issue(
                code="ASM-TP", category="测试", rule="测试点覆盖率", severity="中",
                description=(f"网络测试点覆盖率 {rate * 100:.1f}% < 要求 "
                             f"{coverage_min * 100:.0f}% (已覆盖 {len(tested & set(nets))}/"
                             f"{len(nets)} 网络)，ICT/飞针测试盲区增大"),
                measured=round(rate, 4), required=coverage_min, unit="ratio",
                suggestion="为关键网络补充测试点焊盘，或采用未上电网络测试(ATE)方案"))
    if pad_min:
        for t in tps:
            d = t.get("pad_mm")
            if d is None or d >= pad_min:
                continue
            out.append(make_issue(
                code="ASM-TP", category="测试", rule="测试点尺寸", severity="中",
                description=(f"测试点 {t.get('refdes') or t.get('net')} 直径 "
                             f"{round(d, 3)}mm < {pad_min}mm，探针接触不良"),
                measured=round(d, 4), required=pad_min, unit="mm",
                x=t["x"], y=t["y"], layer=t.get("layer", ""), net=t.get("net", ""),
                suggestion="增大测试点焊盘直径，保证探针接触可靠"))
            if len(out) >= MAX_INSTANCES:
                break
    poly = design.outline or []
    if edge_min and len(poly) >= 3:
        for t in tps:
            d, _cx, _cy = point_poly_edge_dist(t["x"], t["y"], poly)
            if d >= edge_min:
                continue
            out.append(make_issue(
                code="ASM-TP", category="测试", rule="测试点到板边", severity="中",
                description=(f"测试点 {t.get('refdes') or t.get('net')} 到板边 "
                             f"{round(d, 3)}mm < {edge_min}mm，治具/轨道干涉"),
                measured=round(d, 4), required=edge_min, unit="mm",
                x=t["x"], y=t["y"], net=t.get("net", ""),
                suggestion="测试点内移或增加工艺边，保证治具压床可接触"))
            if len(out) >= MAX_INSTANCES:
                break
    return out


# ============================================================
# 注册的检查
# ============================================================
@register_check(
    id="assembly",
    name="装配 / SMT DFM",
    group="装配 DFM",
    description="器件间距/到板边/焊盘对称性/细间距/Via in Pad/丝印上焊盘/基准点/测试点",
    requires=(REQ_DESIGN,),
    order=40,
)
def run_assembly(ctx) -> List[dict]:
    design = ctx.design
    a = lambda key, default: ctx.rule_param("assembly", key, default)  # noqa: E731

    instances: List[dict] = []
    spacing = float(a("min_component_spacing", 0.5) or 0)
    edge = float(a("component_to_edge", 2.0) or 0)
    edge_sev = str(a("component_to_edge_severity", "中") or "中")
    pad_edge = float(a("pad_to_edge", 0.5) or 0)
    sym = float(a("chip_pad_symmetry_ratio", 0.8) or 0)
    pitch = float(a("bga_pitch_min", 0.4) or 0)
    annular = float(a("bga_pad_annular", 0.05) or 0)
    vip_allowed = bool(a("via_in_pad_allowed", False))
    fid_min = int(a("fiducial_min", 2) or 0)
    fid_dia = float(a("fiducial_diameter", 1.0) or 0)
    tp_cov = float(a("test_coverage_min", 0.0) or 0)
    tp_pad = float(a("test_pad_diameter", 0.8) or 0)
    tp_edge = float(a("test_pad_to_edge", 1.27) or 0)

    instances += check_component_spacing(design, spacing)
    instances += check_component_to_edge(design, edge, edge_sev)
    instances += check_pad_to_edge(design, pad_edge)
    instances += check_chip_pad_symmetry(design, sym)
    instances += check_fine_pitch(design, pitch, annular)
    instances += check_via_in_pad(design, vip_allowed)
    instances += check_silk_on_pad(design, ctx.fab_data)
    instances += check_fiducials(design, fid_min, fid_dia)
    instances += check_test_points(design, tp_cov, tp_pad, tp_edge)

    # 参数化装配项 (极性标识 / 钢网 / 统计类)
    param_issues = build_assembly_issues(ctx.params, rules=ctx.rules)
    # 避免与几何检查结果重复: 几何已判定的规则不再走参数化
    geo_rules = {it["rule"] for it in instances}
    param_issues = [it for it in param_issues if it["rule"] not in geo_rules]

    ctx.extras.setdefault("instances", []).extend(instances)
    ctx.extras["assembly_stats"] = {
        "器件数": len(component_boxes(design)),
        "焊盘数": len(design.pads or []),
        "SMD焊盘": sum(1 for p in (design.pads or []) if p.get("is_smd")),
        "测试点数": len(design.testpoints or []),
        "几何问题实例": len(instances),
    }
    ctx.note(f"    装配/SMT DFM: 实例 {len(instances)} 处, 参数化问题 {len(param_issues)} 项")
    return aggregate_by_rule(instances) + param_issues
