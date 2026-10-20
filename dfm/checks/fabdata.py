"""制造数据完整性检查 (Gerber / ODB++ / NC Drill)。

数据源加载阶段 (:func:`dfm.core.source.load_source`) 已完成解析, 本检查直接把
解析产生的完整性问题纳入报告, 并补充统计信息:

  * 缺少必要层 (铜层/阻焊/丝印/外形/钻孔)
  * 单位/格式声明缺失, 坐标越界, 光圈未定义
  * 钻孔无对应焊盘 (孤立孔)、孔径异常
  * 层数与叠层不一致、板框未闭合
"""
from __future__ import annotations

from typing import List

from dfm.core.registry import REQ_FAB, register_check


@register_check(
    id="fabdata",
    name="制造数据完整性",
    group="制造数据",
    description="Gerber / ODB++ / NC Drill 层完整性、单位与光圈、钻孔与焊盘匹配",
    requires=(REQ_FAB,),
    order=5,
)
def run_fabdata(ctx) -> List[dict]:
    issues: List[dict] = []
    for it in ctx.extras.get("fab_integrity_issues") or []:
        it = dict(it)
        it.setdefault("module", "fabdata")
        issues.append(it)

    stats = ctx.fab_stats or {}
    if stats:
        ctx.extras["fab_stats"] = {k: v for k, v in stats.items() if k != "nets"}
        man = ctx.extras.setdefault("manufacturing_extra", {})
        man.setdefault("Gerber图层数", len(stats.get("gerber_layers") or []))
        man.setdefault("钻孔文件数", len(stats.get("drill_files") or []))
        man.setdefault("图形总数", stats.get("feature_count"))
        man.setdefault("孔总数", stats.get("hole_count"))
    ctx.note(f"    制造数据完整性: {len(issues)} 项 "
             f"(层 {len(stats.get('gerber_layers') or [])}, 孔 {stats.get('hole_count')})"
             if stats else f"    制造数据完整性: {len(issues)} 项 (无独立制造数据)")
    return issues
