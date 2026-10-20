"""示例插件 (默认**不加载**: 文件名以 _ 开头会被插件加载器跳过)。

启用方法: 把本文件复制/重命名为 ``my_checks.py`` (去掉开头的下划线),
然后在界面「规则库 → 插件与检查项」点『重新加载插件』, 或重启软件。

插件 = 一个 Python 文件 + 若干个用 ``@register_check`` 装饰的检查函数。
检查函数签名::

    def run(ctx: CheckContext) -> list[dict] | None

ctx 常用属性:
    ctx.design        归一化设计模型 (BrdDesign): clines/pads/vias/components/
                      testpoints/outline/nets/stackup, 坐标单位 mm
    ctx.params        设计参数 (自动量测 + 人工填写合并)
    ctx.capability    当前板厂能力库 (dict, 结构见 rules/capability/*.yaml)
    ctx.fab_data      渲染用图层几何 (Gerber/ODB++/BRD 转换结果)
    ctx.cad_nets      CAD 网表 {net: {refdes.pin}}
    ctx.rules         规则集 (ctx.rule_param('assembly', 'xxx', 默认值) 读阈值)
    ctx.options       factory / board_type / capability_level / copper_um ...
    ctx.extras        写入的统计会合并进报告 Info 页
    ctx.note(msg)     向进度/日志输出一行

返回值: Issue 列表 (用 dfm.core.issue.make_issue 构造)。带 x/y 的 Issue 会进入
版图交叉定位; 同类规则的多处实例会自动按规则汇总为一条并累计数量。
"""
from __future__ import annotations

import math

from dfm.core.issue import make_issue
from dfm.core.registry import REQ_CAPABILITY, REQ_DESIGN, register_check


@register_check(
    id="sample_layer_count",
    name="示例: 铜层数与压合风险",
    group="制造 DFM",
    description="示例插件 — 铜层数超过 12 层时提示压合对位与翘曲风险",
    requires=(REQ_DESIGN,),
    order=90,
)
def check_layer_count(ctx) -> list:
    layers = ctx.design.copper_layers() or []
    limit = int(ctx.rule_param("sample", "max_copper_layers", 12))
    ctx.extras.setdefault("manufacturing_extra", {})["示例-铜层数"] = len(layers)
    if len(layers) > limit:
        return [make_issue(
            code="SMP-01", category="压合", rule="铜层数过多", severity="中",
            description=f"铜层数 {len(layers)} 层 > {limit} 层, 压合对位/翘曲风险上升",
            measured=len(layers), required=limit, unit="层",
            suggestion="与板厂确认层压结构、对位方式(X-ray/铆合)与板厚公差")]
    return []


@register_check(
    id="sample_board_aspect",
    name="示例: 长宽比与传送方向",
    group="装配 DFM",
    description="示例插件 — 板子长宽比过大时提示 SMT 传送与支撑风险",
    requires=(REQ_DESIGN,),
    order=91,
)
def check_board_aspect(ctx) -> list:
    if not ctx.design.outline or len(ctx.design.outline) < 3:
        return []
    xs = [p[0] for p in ctx.design.outline]
    ys = [p[1] for p in ctx.design.outline]
    w, h = (max(xs) - min(xs)), (max(ys) - min(ys))
    if w <= 0 or h <= 0:
        return []
    ratio = max(w, h) / min(w, h)
    limit = float(ctx.rule_param("sample", "max_aspect_ratio", 4.0))
    if ratio > limit:
        return [make_issue(
            code="SMP-02", category="组装", rule="板长宽比过大", severity="低",
            description=(f"外形 {round(w, 1)} x {round(h, 1)}mm, 长宽比 {ratio:.1f} > {limit},"
                         f" SMT 传送易变形"),
            measured=round(ratio, 2), required=limit, unit=":1",
            x=(min(xs) + max(xs)) / 2.0, y=(min(ys) + max(ys)) / 2.0,
            suggestion="增加工艺边/支撑条, 或调整传送方向与炉温曲线")]
    return []


@register_check(
    id="sample_copper_balance",
    name="示例: 上下层铜箔平衡",
    group="制造 DFM",
    description="示例插件 — 上下外层走线数量差异过大时提示翘曲风险",
    requires=(REQ_DESIGN, REQ_CAPABILITY),
    order=92,
)
def check_copper_balance(ctx) -> list:
    layers = ctx.design.copper_layers() or []
    if len(layers) < 2:
        return []
    top, bottom = layers[0], layers[-1]
    n_top = sum(1 for c in ctx.design.clines if c.get("layer") == top)
    n_bot = sum(1 for c in ctx.design.clines if c.get("layer") == bottom)
    total = n_top + n_bot
    if total < 20:
        return []
    diff = abs(n_top - n_bot) / max(n_top, n_bot)
    limit = float(ctx.rule_param("sample", "max_copper_imbalance", 0.6))
    if diff > limit:
        return [make_issue(
            code="SMP-03", category="压合", rule="铜箔分布不平衡", severity="中",
            description=(f"{top} 走线 {n_top} 段 / {bottom} 走线 {n_bot} 段, "
                         f"不平衡度 {diff * 100:.0f}% > {limit * 100:.0f}%, 压合后易翘曲"),
            measured=round(diff, 3), required=limit, unit="ratio",
            suggestion="在稀疏面增加铺铜/网格铜平衡铜量, 或与板厂确认压合参数")]
    return []
