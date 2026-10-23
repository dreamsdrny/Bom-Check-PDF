"""
开短路测试 + 网表一致性对比 (基于归一化几何/网表模型)
=============================================================
两部分 (对应 Valor 的 Netlist / Connectivity 检查):

  A. 几何连通性开短路 (含 XY 定位):
     - 开路 Open : 某逻辑网络的焊盘/过孔/走线在物理上被分割成多个互不相连的
                   连通域, 导致该网络的部分引脚未接通 -> 定位到被孤立的引脚 XY
     - 短路 Short: 不同网络的铜箔在同层实际重叠/接触 -> 定位到接触点 XY
       (短路仅用真实走线/焊盘/过孔几何, 铺铜按包围盒近似仅用于开路连通, 避免误报)

  B. 网表一致性对比:
     - 逻辑网表(导出 net->nodes) vs 物理连通域派生网表
     - 若提供 FAB 网表(ODB++/Gerber X2), 复用 :func:`dfm.io.fab.compare_netlists`
       做 CAD vs FAB 网络/节点比对

性能: 均匀网格空间索引 + 并查集; 对超大板设置元素上限并标注截断。
"""
from typing import Dict, List, Optional, Set, Tuple

from dfm.io.brd_model import BrdDesign, SpatialGrid, sweep_pairs
from dfm.core.issue import IssueCollector, aggregate_by_rule
from dfm.core.registry import REQ_DESIGN, register_check
from dfm.geom.clearance import batch_distances, element_clearance, element_location
from dfm.geom.elements import ELEMENT_POUR, ELEMENT_TRACE, ELEMENT_VIA

EPS = 0.005          # 接触判定容差 (mm)
MAX_ELEMENTS = 400000
MAX_REPORT = 500     # 每类问题最多**留存**实例数 (处数仍全量计数)
#: 接触判定批量计算的分批大小 (限制内存, 同时保持 shapely 向量化收益)
SHORT_BATCH = 200_000


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
    """连通性元素: 包装一个 :class:`dfm.geom.elements.Element` (真实形状 + 极性已合成)。

    旧实现用"线段 + 半径"近似, 且不过滤负片图元 → 内层平面的 anti-pad 被当成铜箔,
    产生大量假短路。现在统一由 :class:`dfm.geom.board.BoardGeometry` 提供元素。
    """
    __slots__ = (
        "cline",
        "el",
        "inside_shape",
        "layer",
        "maxx",
        "maxy",
        "minx",
        "miny",
        "multi",
        "net",
        "pin",
        "refdes",
    )

    def __init__(self, el, net=None, layer=None, multi=False, refdes=None, pin=None,
                 cline=-1):
        self.el = el
        self.net = (el.net or "") if net is None else net
        self.layer = (el.layer or "") if layer is None else layer
        self.minx, self.miny, self.maxx, self.maxy = el.minx, el.miny, el.maxx, el.maxy
        self.multi = multi
        self.refdes = (el.ref or "") if refdes is None else refdes
        self.pin = (el.pin or "") if pin is None else pin
        self.cline = cline
        self.inside_shape = None

    @property
    def kind(self) -> str:
        return self.el.kind


def _build_nodes(design: BrdDesign, include_shapes: bool = True, geometry=None):
    """从共享几何模型构建连通性节点 (真实形状, 负片已合成)。"""
    if geometry is None:
        from dfm.geom.board import BoardGeometry

        geometry = BoardGeometry(design=design)
    nodes: List[_Node] = []
    truncated = False
    for el in geometry.elements:
        multi = el.kind == ELEMENT_VIA           # 过孔跨层连通
        cline = -1 if el.kind != ELEMENT_TRACE else int(el.attrs.get("cline_index", -1))
        nodes.append(_Node(el, multi=multi, cline=cline))
        if len(nodes) >= MAX_ELEMENTS:
            truncated = True
            break
    shapes = [el for el in geometry.elements if el.kind == ELEMENT_POUR] \
        if include_shapes else []
    return nodes, shapes, truncated, geometry


def _dsu_find(mapping: Dict[int, int], x: int) -> int:
    """字典版并查集查找 (带路径压缩), 供逐网络局部连通域使用。"""
    root = x
    while mapping[root] != root:
        root = mapping[root]
    while mapping[x] != root:
        mapping[x], x = root, mapping[x]
    return root


def _layers_compatible(a: _Node, b: _Node) -> bool:
    if a.multi or b.multi:
        return True
    if not a.layer or not b.layer:
        return True
    return a.layer == b.layer


def _touching(a: _Node, b: _Node) -> Tuple[bool, float, float]:
    """按真实几何判定接触 (含矩形/椭圆/多边形铺铜), 返回 (是否接触, x, y)。"""
    d, cx, cy = element_clearance(a.el, b.el)
    return d <= EPS, cx, cy


def analyze_netlist(design: BrdDesign, fab_nets: Optional[Dict[str, Set[str]]] = None,
                    geometry=None, rules=None, instance_cap: int = MAX_REPORT
                    ) -> Tuple[List[dict], dict]:
    """开短路 + 网表一致性; 返回 (问题实例列表, 统计摘要)。

    :param geometry: 可选的共享 :class:`dfm.geom.board.BoardGeometry`
                     (由编排层传入, 避免与制造 DFM 重复建表)
    :param instance_cap: 每规则**留存**实例上限; 处数始终全量计数
    """
    col = IssueCollector(cap=int(instance_cap or MAX_REPORT))

    def emit(rule, severity, description, x=None, y=None, layer="", net="",
             measured=None, required=None, unit="", suggestion="", from_="", to="",
             note=""):
        return col.add("网络比对", rule, severity, description, x=x, y=y, layer=layer,
                       net=net, measured=measured, required=required, unit=unit,
                       suggestion=suggestion, from_=from_, to=to, note=note)

    nodes, shapes, truncated, geom = _build_nodes(design, include_shapes=True,
                                                  geometry=geometry)
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
        "truncated": truncated or geom.stats.truncated,
        "clear_elements_composed": geom.polarity.clear_elements,
    }
    if n == 0:
        emit("网表", "低", "无几何/网表数据, 无法进行开短路测试",
             suggestion="确认 SKILL 导出包含 clines/vias/pads/nets")
        return col.instances, summary

    dsu = DSU(n)
    avg_w = design.etch_width_is_average   # 走线宽为网络平均值: 短路仅按焊盘/过孔真实几何判定
    # extracta 路径带 Allegro 权威 DRC/连通性: 短路以 Allegro DRC 为准, 不用几何近似
    authoritative = (design.connectivity_source == "allegro")

    short_events: List[Tuple[str, str, float, float]] = []
    # 三段式: 广相 → 批量向量化净距 → 仅对确认接触/短路的对算定位点
    batch: List[Tuple[int, int, bool]] = []      # (i, j, report_short)

    def flush_batch() -> None:
        if not batch:
            return
        dists = batch_distances([nd.el for nd in nodes],
                                [(i, j) for i, j, _rs in batch])
        for (i, j, report_short), dist in zip(batch, dists):
            if dist > EPS:
                continue
            a, b = nodes[i], nodes[j]
            same_net = (a.net == b.net)
            # 连通域合并: 同网络必合; 跨网络仅在「按短路处理」时合并
            # (authoritative 时跨网络接触交由 Allegro DRC, 不污染物理连通域计数)
            if same_net or report_short:
                dsu.union(i, j)
            if report_short:
                cx, cy = element_location(a.el, b.el, dist)
                short_events.append((a.net, b.net, cx, cy))
        batch.clear()

    for i, j in sweep_pairs(nodes, tol=EPS):
        a = nodes[i]
        b = nodes[j]
        if not _layers_compatible(a, b):
            continue
        same_net = (a.net == b.net)
        # 宽电源走线按平均宽膨胀会造成跨网络假接触; 此类接触不计短路/不并入连通域,
        # 走线级短路交由 Allegro DRC 判定。
        skip_etch = avg_w and (a.kind == ELEMENT_TRACE or b.kind == ELEMENT_TRACE)
        # 仅有包围盒的铺铜是近似形状: 参与连通性, 但不据此判短路 (避免误报)
        skip_approx = bool(a.el.attrs.get("approx") or b.el.attrs.get("approx"))
        report_short = ((not same_net) and (not skip_etch) and (not skip_approx)
                        and (not authoritative))
        batch.append((i, j, report_short))
        if len(batch) >= SHORT_BATCH:
            flush_batch()
    flush_batch()
    # 注: 铺铜以真实多边形参与接触判定; 仅"只有包围盒"的退化铺铜不据此报短路。

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
        # 局部并查集 (辅助函数在模块级, 显式传入映射, 避免闭包捕获循环变量)
        local = {idx: idx for idx in idxs}

        def lfind(x, _m=local):
            return _dsu_find(_m, x)

        def lunion(a, b, _m=local):
            ra, rb = _dsu_find(_m, a), _dsu_find(_m, b)
            if ra != rb:
                _m[rb] = ra

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
        # 铺铜已作为真实多边形元素参与上面的接触判定与连通合并,
        # 无需再用"包围盒近似"补偿 (仅 bbox 的铺铜见 skip_approx 处理)。
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
            from dfm.io.fab import compare_netlists, netlist_compare_issues
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
                it = dict(it)
                it["category"] = "网络比对"
                # 与 IssueCollector.add 保持一致: 计数与留存同步累加,
                # 否则会被误判为"处数超出留存上限"
                key = ("网络比对", it.get("rule", ""))
                col.counts[key] = col.counts.get(key, 0) + 1
                col.retained[key] = col.retained.get(key, 0) + 1
                col.instances.append(it)
        except Exception:
            pass

    summary["counters"] = {f"{c}|{r}": n for (c, r), n in col.counts.items()}
    summary["retained"] = {f"{c}|{r}": n for (c, r), n in col.retained.items()}
    return col.instances, summary


# ============================================================
# 注册为检查项 (插件式编排入口)
# ============================================================
@register_check(
    id="netlist",
    name="开短路 + 网表一致性",
    group="网表",
    description="几何连通性判定开路/短路 (真实形状+极性, 定位 XY), 并与逻辑/制造网表比对",
    requires=(REQ_DESIGN,),
    order=30,
)
def run_netlist(ctx) -> List[dict]:
    """执行开短路与网表一致性检查 (复用共享几何模型)。"""
    instances, summary = analyze_netlist(ctx.design, fab_nets=ctx.fab_nets or None,
                                         geometry=ctx.geometry, rules=ctx.rules,
                                         instance_cap=int(ctx.options.get("instance_cap")
                                                          or MAX_REPORT))
    for it in instances:
        it.setdefault("module", "netlist")
    ctx.extras.setdefault("instances", []).extend(instances)
    ctx.extras["netlist_summary"] = summary

    counts = {}
    retained = summary.get("retained") or {}
    for key, n in (summary.get("counters") or {}).items():
        cat, _, rule = key.partition("|")
        counts[(cat, rule)] = n
        if n > retained.get(key, 0):
            ctx.add_truncation(f"网表规则 {rule} 处数 {n} 超出留存上限 "
                               f"{retained.get(key, 0)}, 已按真实计数报告")
    if summary.get("truncated"):
        ctx.add_truncation("连通性元素数超出上限, 开短路结论可能不完整")
    ctx.note(f"    开短路: 开路网络 {summary.get('open_net_count')} / "
             f"短路 {summary.get('short_net_count')} / 违规 "
             f"{sum(counts.values())} 处 (留存 {len(instances)})")
    return aggregate_by_rule(instances, counts=counts)
