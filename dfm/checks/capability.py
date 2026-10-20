"""制程能力比对检查: 设计参数 (实测量测或人工填写) vs 板厂能力库。

数据来源两路合并, 人工填写优先:
  1. :func:`dfm.core.source.derive_params` 从几何自动量测 (线宽/孔径/孔环/板厚...)
  2. 用户在界面「设计参数」页填写或导入的 JSON

输出既有逐项 ``items`` (PASS/WARN/FAIL, 报告的 Info 与明细页), 也有标准
Issue 列表 (制造类)。装配与拼板类参数由各自的检查负责, 此处不重复。
"""
from __future__ import annotations

from typing import List

from dfm.core.registry import REQ_CAPABILITY, REQ_PARAMS, register_check


@register_check(
    id="capability",
    name="制程能力 DFM",
    group="制造 DFM",
    description="设计参数 vs 板厂制程能力库 (线宽/线距/孔径/孔环/纵横比/阻焊/丝印/塞孔/表面处理)",
    requires=(REQ_PARAMS, REQ_CAPABILITY),
    order=60,
)
def run_capability(ctx) -> List[dict]:
    from dfm.core.capability import analyze_design

    result = analyze_design(ctx.params, board_type=ctx.board_type,
                            capability_level=ctx.level, factory=ctx.factory,
                            rules=ctx.rules)
    ctx.extras["capability_items"] = result.get("items") or []
    ctx.extras["capability_summary"] = {
        k: v for k, v in result.items()
        if k in ("factory", "factory_label", "board_type_label", "capability_label",
                 "error_count", "warning_count", "passed")
    }
    notes = ((result.get("info_stats") or {}).get("capability_notes") or {})
    if notes:
        ctx.extras.setdefault("capability_notes", {}).update(
            {k: v for k, v in notes.items() if v not in (None, "")})
    issues = list(result.get("issues") or [])
    # 去重: 几何检查已判定并带 XY 定位的规则, 不再由参数化检查重复报告
    covered = set(ctx.extras.get("geometry_rules") or ())
    dropped = [it for it in issues if it.get("rule") in covered]
    issues = [it for it in issues if it.get("rule") not in covered]
    for it in issues:
        it.setdefault("module", "capability")
    ctx.note(f"    制程能力比对: {len(result.get('items') or [])} 项, "
             f"问题 {len(issues)} 项"
             + (f" (其中 {len(dropped)} 项已由几何检查定位, 不重复报告)" if dropped else ""))
    return issues
