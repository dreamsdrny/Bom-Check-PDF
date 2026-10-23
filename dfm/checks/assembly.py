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

from typing import Any, Dict, List, Optional, Sequence, Tuple

from dfm.core.coverage import Coverage
from dfm.core.issue import aggregate_by_rule, build_assembly_issues, make_issue
from dfm.core.registry import REQ_DESIGN, register_check
from dfm.geom import measure as _measure
from dfm.geom.measure import DEFAULT_FIDUCIAL_NAMES
from dfm.io.brd_model import SpatialGrid, point_poly_edge_dist

MAX_INSTANCES = 300


class _Sink(list):
    """问题实例收集容器 (list 子类)。

    与直接用 list 的差别: **处数全量计数, 实例按上限留存**。
    旧实现用 ``len(out) >= MAX_INSTANCES: break`` 把"留存数"当成"处数",
    密集板会报"共 300 处"而真实为上千处, 报告无法作为整改依据。
    """

    def __init__(self, cap: int = MAX_INSTANCES) -> None:
        super().__init__()
        self.cap: int = cap
        self.counts: dict[tuple[str, str], int] = {}
        self.retained: dict[tuple[str, str], int] = {}

    def append(self, issue):
        key = (issue.get("category", "组装"), issue.get("rule", ""))
        self.counts[key] = self.counts.get(key, 0) + 1
        if self.retained.get(key, 0) >= self.cap:
            return
        self.retained[key] = self.retained.get(key, 0) + 1
        super().append(issue)

    def truncated_rules(self) -> List[str]:
        return [f"{c}/{r}" for (c, r), n in self.counts.items()
                if n > self.retained.get((c, r), 0)]

    def total(self) -> int:
        return sum(self.counts.values())


def _cap_reached(out) -> bool:
    """list 模式 (直接调用/测试) 保留上限截断; _Sink 模式持续计数不中断。"""
    return not isinstance(out, _Sink) and len(out) >= MAX_INSTANCES
#: 基准点位号关键字 (缺省值; 实际以规则集 ``assembly.fiducial_names`` 为准)
FIDUCIAL_NAMES = DEFAULT_FIDUCIAL_NAMES


# ============================================================
# 几何量测 (统一实现见 dfm.geom.measure, 与 source.derive_params 同源)
# ============================================================
def component_boxes(design) -> List[Dict[str, Any]]:
    """按 refdes 汇总焊盘包围盒作为器件本体近似 (转发到共享实现)。"""
    return _measure.component_boxes(design)


def _box_gap(a: dict, b: dict) -> Tuple[float, float, float]:
    """两包围盒间距与最近点坐标 (转发到共享实现)。"""
    return _measure.box_gap(a, b)


def _component_side(design, refdes: str) -> str:
    for c in getattr(design, "components", None) or []:
        if str(c.get("refdes", "")) == refdes:
            return str(c.get("side", "")).lower()
    return ""


def _pads_by_refdes(design) -> Dict[str, List[dict]]:
    return _measure.pads_by_refdes(design)


# ============================================================
# 各检查实现 (返回问题实例)
# ============================================================
def check_component_spacing(design, min_gap: float, severity: str = "中",
                            out: Optional[List[dict]] = None) -> List[dict]:
    """器件本体最小间距"""
    boxes = component_boxes(design)
    if len(boxes) < 2 or not min_gap:
        return []
    grid = SpatialGrid(cell=max(min_gap * 4.0, 1.0))
    for i, b in enumerate(boxes):
        grid.insert(b["x0"] - min_gap, b["y0"] - min_gap,
                    b["x1"] + min_gap, b["y1"] + min_gap, i)
    out = [] if out is None else out
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


def check_component_to_edge(design, min_dist: float, severity: str = "中",
                              out: Optional[List[dict]] = None) -> List[dict]:
    """器件本体到成型线距离"""
    poly = design.outline or []
    if len(poly) < 3 or not min_dist:
        return []
    out = [] if out is None else out
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
        if _cap_reached(out):
            break
    return out


def check_pad_to_edge(design, min_dist: float,
                      out: Optional[List[dict]] = None) -> List[dict]:
    """SMD 焊盘到成型线距离 (焊接与成型公差)"""
    poly = design.outline or []
    if len(poly) < 3 or not min_dist:
        return []
    out = [] if out is None else out
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
        if _cap_reached(out):
            break
    return out


def check_chip_pad_symmetry(design, ratio_min: float,
                            out: Optional[List[dict]] = None) -> List[dict]:
    """两端片式元件焊盘对称性 (墓碑效应)"""
    if not ratio_min:
        return []
    out = [] if out is None else out
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
        if _cap_reached(out):
            break
    return out


def check_fine_pitch(design, pitch_min: float, annular_min: float,
                     out: Optional[List[dict]] = None) -> List[dict]:
    """细间距器件 (BGA/QFN) 引脚间距与单边孔环"""
    out = [] if out is None else out
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
        if _cap_reached(out):
            break
    return out


def _min_pitch(pts: List[Tuple[float, float]]) -> Optional[float]:
    """最小相邻焊盘中心距 (转发到共享实现 :func:`dfm.geom.measure.min_pad_pitch`)。"""
    return _measure.min_pad_pitch(pts)


def check_via_in_pad(design, allowed: bool = False,
                     out: Optional[List[dict]] = None) -> List[dict]:
    """过孔落在 SMD 焊盘内 (需树脂塞孔 + 电镀盖帽, 否则锡膏流失/空洞)"""
    smd = [p for p in design.pads if p.get("is_smd") and p.get("x") is not None]
    if not smd or not design.vias:
        return []
    grid = SpatialGrid(cell=1.0)
    for i, p in enumerate(smd):
        w = (p.get("pad_w") or 0.3) / 2.0
        h = (p.get("pad_h") or 0.3) / 2.0
        grid.insert(p["x"] - w, p["y"] - h, p["x"] + w, p["y"] + h, i)
    out = [] if out is None else out
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
        if _cap_reached(out):
            break
    return out


def check_silk_on_pad(design, fab_data: List[dict],
                      out: Optional[List[dict]] = None) -> List[dict]:
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
    out = [] if out is None else out
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
        if _cap_reached(out):
            break
    return out


def check_fiducials(design, min_count: int, min_diameter: float,
                    names: Optional[Sequence[str]] = None,
                    out: Optional[List[dict]] = None) -> List[dict]:
    """基准点 (Fiducial) 数量与尺寸。

    :param names: 位号识别关键字, 取自规则集 ``assembly.fiducial_names``,
                  与 :func:`dfm.geom.measure.fiducials` (参数量测) 保持同一来源,
                  避免"Info 页基准点数"与"问题清单结论"互相矛盾。
    """
    out = [] if out is None else out
    fid = _measure.fiducials(design, names or FIDUCIAL_NAMES)
    found = fid["all"]
    top, bot = len(fid["top"]), len(fid["bottom"])
    if not found:
        out.append(make_issue(
            code="ASM-FD", category="组装", rule="基准点数量", severity="高",
            description="未识别到全局基准点 (Fiducial)，贴片对位精度无法保证",
            measured=0, required=min_count, unit="个",
            suggestion="每面至少布置 2-3 个全局基准点(直径≥1mm，周边 5mm 内无铜/无器件)；"
                       "若位号命名特殊，请在规则集 assembly.fiducial_names 中补充关键字"))
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
            d = _measure.fiducial_diameter(design, str(c.get("refdes", "")))
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
    """基准点焊盘直径 (转发到共享实现)。"""
    return _measure.fiducial_diameter(design, refdes)


def check_test_points(design, coverage_min: float, pad_min: float,
                      edge_min: float,
                      out: Optional[List[dict]] = None) -> List[dict]:
    """测试点覆盖率 / 尺寸 / 到板边"""
    out = [] if out is None else out
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
            if _cap_reached(out):
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
            if _cap_reached(out):
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
    a = lambda key, default: ctx.rule_param("assembly", key, default)

    sink = _Sink(cap=int(ctx.options.get("instance_cap") or MAX_INSTANCES))
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
    fid_names = a("fiducial_names", None) or FIDUCIAL_NAMES
    tp_cov = float(a("test_coverage_min", 0.0) or 0)
    tp_pad = float(a("test_pad_diameter", 0.8) or 0)
    tp_edge = float(a("test_pad_to_edge", 1.27) or 0)

    # 覆盖率: 阈值为 0 表示该项未启用 (记入未判定, 不静默跳过)
    cov = Coverage(check_id="assembly")
    for cat, rule, limit in (("组装", "器件间距", spacing),
                             ("组装", "器件到板边", edge),
                             ("组装", "焊盘到板边", pad_edge),
                             ("组装", "片式元件焊盘对称性", sym),
                             ("组装", "BGA焊盘", pitch),
                             ("组装", "基准点数量", fid_min),
                             ("组装", "基准点尺寸", fid_dia),
                             ("测试", "测试点尺寸", tp_pad),
                             ("测试", "测试点到板边", tp_edge)):
        if limit:
            cov.judged(cat, rule, limit, source="规则集 assembly")
        else:
            cov.missed(cat, rule, "规则集阈值为 0 (未启用)")
    if tp_cov:
        cov.judged("测试", "测试点覆盖率", tp_cov, source="规则集 assembly")
    else:
        cov.missed("测试", "测试点覆盖率", "规则集 test_coverage_min=0 (未启用)")
    if not (getattr(design, "outline", None) and len(design.outline) >= 3):
        cov.missed("组装", "器件到板边", "无板框轮廓, 无法判定到板边距离")
        cov.missed("组装", "焊盘到板边", "无板框轮廓, 无法判定到板边距离")
        cov.missed("测试", "测试点到板边", "无板框轮廓, 无法判定到板边距离")
    if not any(str(d.get("name", "")).lower().find("silk") >= 0 or
               str(d.get("name", "")).lower().find("gto") >= 0
               for d in (ctx.fab_data or [])):
        cov.missed("丝印", "丝印上焊盘", "制造数据中无丝印层")

    check_component_spacing(design, spacing, out=sink)
    check_component_to_edge(design, edge, edge_sev, out=sink)
    check_pad_to_edge(design, pad_edge, out=sink)
    check_chip_pad_symmetry(design, sym, out=sink)
    check_fine_pitch(design, pitch, annular, out=sink)
    check_via_in_pad(design, vip_allowed, out=sink)
    check_silk_on_pad(design, ctx.fab_data, out=sink)
    check_fiducials(design, fid_min, fid_dia, names=fid_names, out=sink)
    check_test_points(design, tp_cov, tp_pad, tp_edge, out=sink)
    instances = list(sink)

    # 参数化装配项 (极性标识 / 统计类)
    param_issues = build_assembly_issues(ctx.params, rules=ctx.rules)
    # 避免与几何检查结果重复: 几何已判定的规则不再走参数化
    geo_rules = {it["rule"] for it in instances}
    param_issues = [it for it in param_issues if it["rule"] not in geo_rules]

    ctx.extras.setdefault("instances", []).extend(instances)
    ctx.extras["assembly_stats"] = {
        "器件数": len(component_boxes(design)),
        "焊盘数": len(getattr(design, "pads", None) or []),
        "SMD焊盘": sum(1 for p in (getattr(design, "pads", None) or []) if p.get("is_smd")),
        "测试点数": len(getattr(design, "testpoints", None) or []),
        "违规处数": sink.total(),
        "留存实例": len(instances),
    }
    ctx.set_coverage(cov)
    for rule in sink.truncated_rules():
        ctx.add_truncation(f"装配规则 {rule} 违规处数超出留存上限 {sink.cap}, "
                           f"已按真实计数报告")
    ctx.note(f"    装配/SMT DFM: 违规 {sink.total()} 处 (留存 {len(instances)}), "
             f"参数化问题 {len(param_issues)} 项, 覆盖率 {cov.ratio * 100:.0f}%")
    return aggregate_by_rule(instances, counts=dict(sink.counts)) + param_issues
