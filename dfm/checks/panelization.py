"""拼板 / 工艺边 DFM 检查 (参数化)。

> 说明: 本版**不含**拼板排版设计器 (已按产品定位移除), 只保留 DFM 判定:
> 拼板尺寸是否超出板厂工作板范围、工艺边宽度、Mark 点数量与位置、
> 定位孔数量、板材利用率、是否未拼板导致传送撞件。

阈值来自规则集 ``panelization`` 段 (rules/checks.yaml), 板厂工作板范围来自
制程能力库 ``panel`` 段。
"""
from __future__ import annotations

from typing import List

from dfm.core.issue import build_panel_issues
from dfm.core.registry import REQ_PARAMS, register_check


@register_check(
    id="panelization",
    name="拼板 / 工艺边 DFM",
    group="拼板",
    description="拼板尺寸/工艺边/Mark点/定位孔/板材利用率 (参数化判定)",
    requires=(REQ_PARAMS,),
    order=50,
)
def run_panelization(ctx) -> List[dict]:
    issues: List[dict] = build_panel_issues(ctx.params, ctx.capability, rules=ctx.rules)
    for it in issues:
        it.setdefault("module", "panelization")
    if issues:
        ctx.note(f"    拼板/工艺边: {len(issues)} 项")
    return issues
