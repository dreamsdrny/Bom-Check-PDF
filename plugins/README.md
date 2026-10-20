# 插件目录

把 `.py` 文件（或以 `__init__.py` 开头的包目录）放进本目录，重启软件或在
「规则库 → 插件与检查项」页点 **重新加载插件** 即可生效。

- 文件名以 `_` 或 `.` 开头的会被跳过（用于随包分发的示例，避免默认干扰分析）。
- 用户级插件目录：`%APPDATA%\EvanDFM\plugins\`（升级软件不会丢失）。
- 可用环境变量 `EVAN_DFM_PLUGINS` 指定额外目录。

## 最小示例

```python
from dfm.core.issue import make_issue
from dfm.core.registry import REQ_DESIGN, register_check

@register_check(id="my_check", name="我的检查", group="制造 DFM",
                description="一句话说明", requires=(REQ_DESIGN,), order=95)
def run(ctx):
    issues = []
    for v in ctx.design.vias:
        if v.get("drill_mm") and v["drill_mm"] < 0.15:
            issues.append(make_issue(
                code="MY-01", category="钻孔", rule="超小孔径", severity="危险",
                description=f"孔径 {v['drill_mm']}mm 过小",
                measured=v["drill_mm"], required=0.15, unit="mm",
                x=v["x"], y=v["y"], suggestion="增大孔径或改用激光孔"))
    return issues
```

完整说明见 `docs/04-二次开发指南.md`，可直接复制 `_sample_check.py` 改名启用。
