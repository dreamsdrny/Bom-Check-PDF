"""CAD 网表 vs 制造数据网表比对 (Open / Short)。

用途: 客户下发 Gerber/ODB++ 时, 用 CAD 侧网表核对制造数据是否漏铜、短接、
网络丢失 — 这是投板前最后一道网表防线。

CAD 网表 JSON 格式 (任一)::

    {"GND": ["R1.1", "C2.2"], "VCC": [...]}
    [{"net": "GND", "nodes": ["R1.1", "C2.2"]}, ...]

制造数据侧网表来源优先级:
  1. Gerber X2 属性 (%TO.N/%TO.P) 或 ODB++ netlist 文件
  2. 由设计模型 (BrdDesign) 的节点汇总
"""
from __future__ import annotations

from typing import List

from dfm.core.registry import REQ_CAD, register_check


@register_check(
    id="fabnet",
    name="CAD vs FAB 网络比对",
    group="网表",
    description="CAD 网表与制造数据网表比对, 判定开路(Open)/短路(Short)/网络丢失",
    requires=(REQ_CAD,),
    order=70,
)
def run_fabnet(ctx) -> List[dict]:
    from dfm.io.fab import compare_netlists, netlist_compare_issues

    fab_nets = dict(ctx.fab_nets or {})
    if not fab_nets and ctx.design is not None:
        fab_nets = {
            name: {f"{nd['refdes']}.{nd['pin']}" for nd in info.get("nodes") or []
                   if nd.get("refdes")}
            for name, info in ctx.design.nets.items()
        }
    if not fab_nets:
        ctx.extras["fabnet_summary"] = {
            "note": "制造数据无网络信息 (缺少 X2 属性/ODB 网表), 跳过比对"}
        return []

    cmp = compare_netlists(ctx.cad_nets, fab_nets)
    issues: List[dict] = []
    for it in netlist_compare_issues(cmp):
        it = dict(it)
        it.setdefault("module", "fabnet")
        issues.append(it)
    ctx.extras["fabnet_summary"] = {k: v for k, v in cmp.items()
                                    if k not in ("node_open", "node_short")}
    ctx.note(f"    CAD vs FAB: CAD {cmp['cad_net_count']} / FAB {cmp['fab_net_count']} 网络, "
             f"Open {cmp['open_count']} / Short {cmp['short_count']} "
             f"{'PASS' if cmp['passed'] else 'FAIL'}")
    return issues
