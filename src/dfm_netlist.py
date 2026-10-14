"""
BRD-AI: 开短路测试 + 网表一致性对比 (基于 SKILL 导出的几何/网表)
=============================================================
两部分 (对应 Valor 的 Netlist / Connectivity 检查):

  A. 几何连通性开短路 (含 XY 定位):
     - 开路 Open : 某逻辑网络的焊盘/过孔/走线在物理上被分割成多个互不相连的
                   连通域, 导致该网络的部分引脚未接通 -> 定位到被孤立的引脚 XY
     - 短路 Short: 不同网络的铜箔在同层实际重叠/接触 -> 定位到接触点 XY
       (短路仅用真实走线/焊盘/过孔几何, 铺铜按包围盒近似仅用于开路连通, 避免误报)

  B. 网表一致性对比:
     - 逻辑网表(SKILL 导出 net->nodes) vs 物理连通域派生网表
     - 若提供 FAB 网表(ODB++/Gerber), 复用 fab_data_reader.compare_netlists 做
       CAD vs FAB 网络/节点比对

性能: 均匀网格空间索引 + 并查集; 对超大板设置元素上限并标注截断。
"""
from typing import Dict, List, Optional, Set, Tuple

from src.brd_geometry import BrdDesign, SpatialGrid, seg_seg_dist, sweep_pairs
from src.dfm_rules import make_issue

EPS = 0.005          # 接触判定容差 (mm)
MAX_ELEMENTS = 400000
MAX_REPORT = 500     # 每类问题最多输出实例数


class DSU:
    def __init__(self, n: int):
        self.p = list(range(n))
        self.r = [0] * n

    def find(self, x: int) -> int:
        p = self.p
        while p[x] != x:
            p[x] = p[p[x]]
            x = p[x]
        return x

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.r[ra] < self.r[rb]:
            ra, rb = rb, ra
        self.p[rb] = ra
        if self.r[ra] == self.r[rb]:
            self.r[ra] += 1
        return True


class _Node:
    """连通性元素: 线段(p1->p2)+半径; 焊盘/过孔退化为中心点"""
    __slots__ = ("net", "layer", "x1", "y1", "x2", "y2", "r", "multi",
                 "minx", "miny", "maxx", "maxy", "refdes", "pin", "cline", "inside_shape")

    def __init__(self, net, layer, x1, y1, x2, y2, r, multi=False,
                 refdes="", pin="", cline=-1):
        self.net = net or ""
        self.layer = layer or ""
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
        self.r = r or 0.0
        self.multi = multi
        self.minx = min(x1, x2) - self.r
        self.maxx = max(x1, x2) + self.r
        self.miny = min(y1, y2) - self.r
        self.maxy = max(y1, y2) + self.r
        self.refdes = refdes
        self.pin = pin
        self.cline = cline
        self.inside_shape = None


def _build_nodes(design: BrdDesign, include_shapes: bool):
    nodes: List[_Node] = []
    shapes: List[dict] = []
    for ci, cl in enumerate(design.clines):
        pts = cl["points"]
        r = (cl["width"] or 0.0) / 2.0
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            nodes.append(_Node(cl["net"], cl["layer"], x1, y1, x2, y2, r,
                               multi=False, cline=ci))
            if len(nodes) >= MAX_ELEMENTS:
                return nodes, shapes
    for p in design.pads:
        if p["x"] is None:
            continue
        w = p["pad_w"] or p["pad_h"] or 0.3
        multi = not p["is_smd"]          # 插件焊盘跨层连通
        nodes.append(_Node(p["net"], p["layer"], p["x"], p["y"], p["x"], p["y"],
                           w / 2.0, multi=multi, refdes=p["refdes"], pin=p["pin"]))
        if len(nodes) >= MAX_ELEMENTS:
            return nodes, shapes
    for v in design.vias:
        if v["x"] is None:
            continue
        r = (v["pad_mm"] or 0.5) / 2.0
        nodes.append(_Node(v["net"], v["start_layer"] or v["end_layer"], v["x"], v["y"],
                           v["x"], v["y"], r, multi=True))
        if len(nodes) >= MAX_ELEMENTS:
            return nodes, shapes
    if include_shapes:
        for s in design.shapes:
            bb = s["bbox"]
            if bb[0] is None:
                continue
            shapes.append(s)
    return nodes, shapes


def _layers_compatible(a: _Node, b: _Node) -> bool:
    if a.multi or b.multi:
        return True
    if not a.layer or not b.layer:
        return True
    return a.layer == b.layer


def _touching(a: _Node, b: _Node) -> Tuple[bool, float, float]:
    d, cx, cy = seg_seg_dist((a.x1, a.y1), (a.x2, a.y2), (b.x1, b.y1), (b.x2, b.y2))
    return (d - a.r - b.r) <= EPS, cx, cy


def analyze_netlist(design: BrdDesign, fab_nets: Optional[Dict[str, Set[str]]] = None
                    ) -> Tuple[List[dict], dict]:
    """开短路 + 网表一致性; 返回 (issues, summary)"""
    issues: List[dict] = []
    counters = {"开路(Open)": 0, "短路(Short)": 0, "网表": 0}

    def emit(rule, severity, description, x=None, y=None, layer="", net="",
             measured=None, required=None, unit="", suggestion="", from_="", to="",
             note=""):
        if counters.get(rule, 0) >= MAX_REPORT:
            return
        counters[rule] = counters.get(rule, 0) + 1
        prefix = "NET" if rule in ("开路(Open)", "短路(Short)") else "NLC"
        issues.append(make_issue(
            code=f"{prefix}-{counters[rule]:04d}", category="网络比对", rule=rule,
            severity=severity, description=description, measured=measured,
            required=required, unit=unit, layer=layer, x=x, y=y,
            suggestion=suggestion, from_=from_, to=to, note=note, net=net,
        ))

    nodes, shapes = _build_nodes(design, include_shapes=True)
    n = len(nodes)
    summary = {
        "logical_net_count": len(design.nets),
        "node_count": sum(len(v["nodes"]) for v in design.nets.values()),
        "element_count": n,
        "physical_component_count": 0,
        "open_net_count": 0,
        "short_net_count": 0,
        "nodes_without_copper": 0,
        "short_events": 0,
        "open_events": 0,
        "truncated": n >= MAX_ELEMENTS,
    }
    if n == 0:
        emit("网表", "低", "无几何/网表数据, 无法进行开短路测试",
             suggestion="确认 SKILL 导出包含 clines/vias/pads/nets")
        return issues, summary

    dsu = DSU(n)
    avg_w = design.etch_width_is_average   # 走线宽为网络平均值: 短路仅按焊盘/过孔真实几何判定
    # extracta 路径带 Allegro 权威 DRC/连通性: 短路以 Allegro DRC 为准, 不用几何近似
    authoritative = (design.connectivity_source == "allegro")

    short_events: List[Tuple[str, str, float, float]] = []
    for i, j in sweep_pairs(nodes, tol=EPS):
        a = nodes[i]
        b = nodes[j]
        if not _layers_compatible(a, b):
            continue
        ok, cx, cy = _touching(a, b)
        if not ok:
            continue
        same_net = (a.net == b.net)
        # 宽电源走线按平均宽膨胀会造成跨网络假接触; 此类接触不计短路/不并入连通域,
        # 走线级短路交由 Allegro DRC 判定。
        skip_etch = avg_w and (a.cline >= 0 or b.cline >= 0)
        report_short = (not same_net) and (not skip_etch) and (not authoritative)
        # 连通域合并: 同网络必合; 跨网络仅在「按短路处理」时合并 (authoritative 时
        # 跨网络接触交由 Allegro DRC, 不污染物理连通域计数)
        if same_net or report_short:
            dsu.union(i, j)
        if report_short:
            short_events.append((a.net, b.net, cx, cy))
    # 注: 铺铜(包围盒)连通采用「同网络且中心落入包围盒即视为连通」的近似, 在下面
    #     按网络重建连通域时实现; 短路仅用真实走线/焊盘/过孔几何, 避免铺铜误报。

    # ---------- 短路 (几何) ----------
    # 去重: 同一对网络只保留少量代表点
    seen_short: Set[Tuple[str, str]] = set()
    for netA, netB, x, y in short_events:
        key = tuple(sorted((netA, netB)))
        dup = key in seen_short
        if not dup:
            seen_short.add(key)
        emit("短路(Short)", "危险",
             f"网络 {netA} 与 {netB} 铜箔接触/重叠 (疑似短路)"
             + (" [同网络对多处]" if dup else ""),
             x=x, y=y, net=netA, from_=netA, to=netB,
             suggestion="检查残铜/孤立铜/铺铜溢出, 修正短路")
    summary["short_events"] = len(short_events)
    summary["short_net_count"] = len(seen_short)

    # ---------- 开路 (Open) ----------
    open_events = 0
    open_nets: List[str] = []
    nodes_wo_copper = 0
    use_allegro_opens = (design.connectivity_source == "allegro")
    if use_allegro_opens:
        # 采用 Allegro 连通性(ratsnest)的权威开路结果, 逐条定位 XY
        seen_open = set()
        for o in design.opens:
            open_events += 1
            if o.get("net"):
                seen_open.add(o["net"])
            node_desc = (f"{o['refdes']}.{o['pin']}"
                         if o.get("refdes") and o.get("pin") else "")
            emit("开路(Open)", "危险",
                 f"网络 {o.get('net') or '?'} 存在未连接(ratsnest): "
                 f"{o.get('node1','')} ↔ {o.get('node2','')}"
                 + (f"  @ {node_desc}" if node_desc else ""),
                 x=o.get("x"), y=o.get("y"), net=o.get("net", ""),
                 from_=o.get("node1", ""), to=o.get("node2", ""),
                 suggestion="补走线/过孔接通该未连接(ratsnest)")
        open_nets = sorted(seen_open)
    # 几何开路 (SKILL 导出无 Allegro 连通性时): 需要网格做同网络连通域重建
    grid = None
    if not use_allegro_opens:
        grid = SpatialGrid(cell=0.8)
        for i, nd in enumerate(nodes):
            grid.insert(nd.minx, nd.miny, nd.maxx, nd.maxy, i)
    for net_name, net in ({} if use_allegro_opens else design.nets).items():
        idxs = [i for i, nd in enumerate(nodes) if nd.net == net_name]
        if not idxs:
            # 逻辑网络无任何铜箔
            if net["nodes"]:
                nodes_wo_copper += len(net["nodes"])
                nd0 = net["nodes"][0]
                open_events += 1
                open_nets.append(net_name)
                emit("开路(Open)", "危险",
                     f"网络 {net_name} 无任何铜箔几何 (逻辑存在, 物理缺失), "
                     f"涉及 {len(net['nodes'])} 个引脚",
                     x=nd0.get("x"), y=nd0.get("y"), net=net_name,
                     suggestion="核对该网络是否漏布/被删除")
            continue
        # 局部并查集
        local = {idx: idx for idx in idxs}

        def lfind(x):
            while local[x] != x:
                local[x] = local[local[x]]
                x = local[x]
            return x

        def lunion(a, b):
            ra, rb = lfind(a), lfind(b)
            if ra != rb:
                local[rb] = ra

        pos = {idx: k for k, idx in enumerate(idxs)}  # noqa: F841 (索引备用)
        for i in idxs:
            a = nodes[i]
            for j in grid.query(a.minx - EPS, a.miny - EPS, a.maxx + EPS, a.maxy + EPS):
                if nodes[j].net != net_name:
                    continue
                b = nodes[j]
                if not _layers_compatible(a, b):
                    continue
                ok, _cx, _cy = _touching(a, b)
                if ok:
                    lunion(i, j)
        # 铺铜连通(近似): 同网络、中心落入铺铜包围盒的元素并入同一域
        for s in shapes:
            if s["net"] != net_name or s["bbox"][0] is None:
                continue
            bb = s["bbox"]
            anchor = None
            for i in idxs:
                a = nodes[i]
                if a.layer != s["layer"]:
                    continue
                cx = (a.x1 + a.x2) / 2.0
                cy = (a.y1 + a.y2) / 2.0
                if bb[0] - EPS <= cx <= bb[2] + EPS and bb[1] - EPS <= cy <= bb[3] + EPS:
                    if anchor is None:
                        anchor = i
                    else:
                        lunion(anchor, i)
        # 连通域 -> 该域包含的引脚(refdes.pin)
        pin_node_index: Dict[str, int] = {}
        for i in idxs:
            nd = nodes[i]
            if nd.refdes and nd.pin:
                pin_node_index[f"{nd.refdes}.{nd.pin}"] = i
        comp_of_pin: Dict[str, int] = {}
        for key, i in pin_node_index.items():
            comp_of_pin[key] = lfind(i)
        # 逻辑节点覆盖检查
        comps_used: Dict[int, List[dict]] = {}
        for nd in net["nodes"]:
            key = f"{nd['refdes']}.{nd['pin']}"
            if not nd["refdes"] or not nd["pin"]:
                continue
            if key in comp_of_pin:
                comps_used.setdefault(comp_of_pin[key], []).append(nd)
            else:
                # 逻辑节点无对应焊盘铜箔 -> 开路(未接通)
                nodes_wo_copper += 1
                open_events += 1
                emit("开路(Open)", "危险",
                     f"网络 {net_name} 引脚 {key} 无对应焊盘/铜箔 (疑似开路/未连接)",
                     x=nd.get("x"), y=nd.get("y"), net=net_name,
                     from_=net_name, to=key,
                     suggestion="检查该引脚是否漏接/焊盘缺失")
        if len(comps_used) > 1:
            open_nets.append(net_name)
            # 保留最大连通域, 其余为被孤立部分
            biggest = max(comps_used.items(), key=lambda kv: len(kv[1]))
            for comp, pins in comps_used.items():
                if comp == biggest[0]:
                    continue
                nd = pins[0]
                open_events += 1
                pin_labels = [f"{p['refdes']}.{p['pin']}" for p in pins
                              if p.get("refdes") and p.get("pin")]
                shown = ",".join(pin_labels[:3]) + ("…" if len(pin_labels) > 3 else "")
                emit("开路(Open)", "危险",
                     f"网络 {net_name} 物理上被分割为 {len(comps_used)} 个连通域, "
                     f"引脚 {shown} 未与主连通域接通 (疑似开路)",
                     x=nd.get("x"), y=nd.get("y"), net=net_name,
                     from_=net_name, to=f"{len(pins)} pins",
                     suggestion="补走线/过孔接通被孤立的铜箔 island")
    summary["open_events"] = open_events
    summary["open_net_count"] = len(open_nets)
    summary["nodes_without_copper"] = nodes_wo_copper

    # 物理连通域数量 (全局)
    summary["physical_component_count"] = len({dsu.find(i) for i in range(n)})

    # ---------- 网表一致性对比 ----------
    passed = (open_events == 0 and len(short_events) == 0)
    consis = (f"网表一致性: 逻辑网络 {summary['logical_net_count']} 个, "
              f"物理连通域 {summary['physical_component_count']} 个, "
              f"开路网络 {summary['open_net_count']} 个, "
              f"短路网络对 {summary['short_net_count']} 对, "
              f"无铜节点 {summary['nodes_without_copper']} 个"
              + (" (数据量大, 已截断)" if summary["truncated"] else ""))
    emit("网表一致性", "低" if passed else "危险", consis,
         net="", suggestion="无需处理" if passed else "修正上述开路/短路后复检",
         measured=summary["logical_net_count"],
         required=summary["physical_component_count"], unit="net")

    # ---------- CAD vs FAB (若提供制造网表) ----------
    if fab_nets:
        try:
            from src.fab_data_reader import compare_netlists, netlist_compare_issues
            cad = {name: {f"{nd['refdes']}.{nd['pin']}" for nd in v["nodes"]
                          if nd["refdes"] and nd["pin"]}
                   for name, v in design.nets.items()}
            cmp = compare_netlists(cad, fab_nets)
            summary["fab_compare"] = {
                "cad_net_count": cmp["cad_net_count"],
                "fab_net_count": cmp["fab_net_count"],
                "matched_nets": cmp["matched_nets"],
                "open_count": cmp["open_count"],
                "short_count": cmp["short_count"],
                "passed": cmp["passed"],
            }
            for it in netlist_compare_issues(cmp):
                it["category"] = "网络比对"
                issues.append(it)
        except Exception:  # noqa: BLE001
            pass

    return issues, summary
