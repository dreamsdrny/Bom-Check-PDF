"""DFM 分析编排 (Pipeline) — 全软件唯一的分析入口。

流程::

    配置 → 加载规则集 → 加载数据源(归一化) → 载入板厂能力库
         → 按注册表顺序执行检查(插件同样参与) → 规则集改判 → 汇总评分
         → 产出 analysis 字典 (报告 / 界面 / CLI 共用)

用法::

    from dfm import analyze_file
    result = analyze_file("D:/board.brd", factory="嘉立创", board_type="rigid",
                          capability_level="standard", copper_um=35)
    print(result["risk"]["score"], result["issue_summary"])

``analysis`` 关键字段::

    issues           问题清单(每规则一条, 含 count / locations / XY / 建议)
    instances        全部问题实例(含 XY, 供版图交叉定位)
    issue_summary    分级统计 {危险/高/中/低/关闭/总计}
    risk             评分 {score, risk_level, verdict, yield_estimate, by_severity}
    board_meta       板名/版本/EDA工具/规则集/时间/提交人
    info_stats       Info 页统计 {manufacturing, assembly, capability_notes}
    fab_data         渲染用图层几何
    checks_run / checks_skipped   已执行 / 被跳过的检查及原因
    extras           各检查写入的统计 (netlist_summary / gerber_meta / net_classes ...)
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from dfm.core import registry
from dfm.core.issue import (SEVERITY_ORDER, compute_risk_score, renumber_issues,
                            summarize_issues)
from dfm.core.rules_config import RulesConfig, evaluate_custom_rules
from dfm.core.source import SourceData, load_source

MAX_INSTANCES_IN_RESULT = 20000

#: 产生带 XY 定位实例的几何类检查 (参数化检查会跳过这些规则以免重复报告)
GEOMETRY_CHECKS = ("fabrication", "netlist", "assembly")


def _check_modules() -> List[tuple]:
    """[(检查id, 显示名)] — 供界面勾选与命令行 --modules 校验。"""
    registry.load_builtin_checks()
    return [(c.id, c.name) for c in registry.list_checks()]


#: 兼容旧接口: 检查模块清单
CHECK_MODULES = _check_modules()


def list_modules() -> List[dict]:
    """全部检查项的元数据 (id/名称/分组/依赖/顺序/来源)。"""
    registry.load_builtin_checks()
    return [c.to_dict() for c in registry.list_checks()]


def analyze(cfg: Dict[str, Any],
            progress: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """执行一次完整 DFM 分析。

    :param cfg: 分析配置, 关键字段::

        source_type      brd | json | gerber | odb | params | none (缺省自动识别)
        source_path      主数据源路径 (.brd / .json / Gerber 目录 / ODB++ 目录)
        fab_path         附加制造数据 (Gerber/ODB++/Drill)
        cad_nets         CAD 网表 JSON 路径或 dict
        design_params    设计参数 JSON 路径或 dict
        factory          板厂 id / 中文名 / 别名
        board_type       rigid | flex
        capability_level standard | automotive | extreme (或能力库自定义等级)
        copper_um        成品铜厚 (um)
        copper_oz        成品铜厚 (oz), 缺省由 copper_um 推算
        board_thickness  板厚 (mm), Gerber 源需人工提供才能判纵横比
        modules          启用的检查 id 列表 (缺省=全部已启用检查)
        rules_path       规则集 YAML 路径 (缺省按搜索路径查找)
        load_plugins     是否加载插件 (默认 True)
        extracta         extracta.exe 路径 (BRD 源可选)
        customer/submitter/revision  报告元数据

    :param progress: 进度回调 ``fn(message: str)``, 界面与 CLI 共用
    :return: analysis 字典
    """
    def note(msg: str) -> None:
        if progress:
            progress(msg)

    cfg = dict(cfg or {})
    rules = RulesConfig.load(cfg.get("rules_path"))
    registry.load_builtin_checks()
    plugins_report: List[dict] = []
    if cfg.get("load_plugins", True):
        plugins_report = registry.load_plugins()
        loaded = [p for p in plugins_report if p.get("checks")]
        if loaded:
            note(f"已加载插件: {', '.join(p['name'] for p in loaded)}")
        for p in plugins_report:
            if p.get("status") == "error":
                note(f"[WARN] 插件加载失败 {os.path.basename(p['path'])}: {p['error']}")

    # ---------- 1. 数据源 ----------
    note("[1/4] 加载数据源 ...")
    src: SourceData = load_source(cfg, progress=progress)
    for w in src.warnings:
        note(f"    [提示] {w}")

    # ---------- 2. 能力库 ----------
    from dfm.core.capability import (factory_label, level_label, load_capability,
                                     load_capability_db, normalize_factory,
                                     normalize_level)

    factory = normalize_factory(cfg.get("factory") or "jlcpcb")
    board_type = str(cfg.get("board_type") or "rigid")
    level = normalize_level(cfg.get("capability_level") or "standard")
    copper_um = float(cfg.get("copper_um") or 35.0)
    copper_oz = cfg.get("copper_oz")
    copper_oz = float(copper_oz) if copper_oz else max(1.0, round(copper_um / 35.0, 1))
    capability = load_capability(board_type, level, factory=factory)
    note(f"[2/4] 规则集: {factory_label(factory)} / {board_type} / "
         f"{level_label(board_type, level, factory=factory)} / 铜厚 {copper_um}um")

    # ---------- 3. 执行检查 ----------
    ctx = registry.CheckContext(
        params=src.params, design=src.design, capability=capability,
        fab_data=src.fab_data, fab_stats=src.fab_stats, cad_nets=src.cad_nets,
        fab_nets=src.fab_nets, rules=rules,
        options={
            "factory": factory, "board_type": board_type, "capability_level": level,
            "copper_um": copper_um, "copper_oz": copper_oz,
            "board_thickness": cfg.get("board_thickness"),
        },
        progress=progress,
    )
    ctx.extras["fab_integrity_issues"] = src.fab_issues
    # 数据源阶段的统计 (叠层/铜层/Gerber 层识别/量测参数) 一并带入报告
    for key, value in src.extras.items():
        if isinstance(value, dict):
            ctx.extras.setdefault(key, {}).update(value)
        else:
            ctx.extras.setdefault(key, value)

    requested = cfg.get("modules")
    requested = set(requested) if requested else None
    issues: List[dict] = []
    checks_run: List[str] = []
    checks_skipped: List[dict] = []
    errors: Dict[str, str] = {}

    specs = registry.list_checks()
    total = len(specs)
    note(f"[3/4] 执行检查 ({total} 项已注册) ...")
    for i, spec in enumerate(specs, start=1):
        if requested is not None and spec.id not in requested:
            checks_skipped.append({"id": spec.id, "name": spec.name, "reason": "未勾选"})
            continue
        if not spec.enabled or not rules.check_enabled(spec.id):
            checks_skipped.append({"id": spec.id, "name": spec.name, "reason": "规则集已停用"})
            continue
        missing = spec.missing_requires(ctx)
        if missing:
            checks_skipped.append({"id": spec.id, "name": spec.name,
                                   "reason": f"缺少数据: {', '.join(missing)}"})
            continue
        note(f"    ({i}/{total}) {spec.name} [{spec.id}] ...")
        try:
            produced = spec.run(ctx) or []
        except Exception as exc:  # noqa: BLE001
            import traceback
            errors[f"{spec.id}_error"] = f"{exc}"
            ctx.extras.setdefault("check_errors", {})[spec.id] = traceback.format_exc()
            note(f"    [ERROR] {spec.name} 执行失败: {exc}")
            continue
        covered = ctx.extras.setdefault("geometry_rules", set())
        for it in produced:
            it = dict(it)
            it.setdefault("module", spec.id)
            it.setdefault("check_name", spec.name)
            issues.append(it)
            # 记录已由几何检查判定的规则, 供参数化检查去重 (几何结论更权威且带 XY)
            if spec.id in GEOMETRY_CHECKS or it.get("x") is not None:
                covered.add(it.get("rule"))
        checks_run.append(spec.id)

    # ---------- 3b. 声明式自定义规则 ----------
    if rules.check_enabled("custom_rules"):
        custom = evaluate_custom_rules(ctx.params, config=rules)
        for it in custom:
            it.setdefault("module", "custom_rules")
            issues.append(it)
        if custom:
            checks_run.append("custom_rules")
            note(f"    自定义规则: {len(custom)} 项")

    # ---------- 4. 汇总 ----------
    note("[4/4] 规则集改判 / 汇总评分 ...")
    issues = rules.apply(issues)
    issues = renumber_issues(issues)
    summary = summarize_issues(issues)
    risk = compute_risk_score(issues, rules.scoring())

    instances = list(ctx.extras.get("instances") or [])
    instances.sort(key=lambda x: SEVERITY_ORDER.get(x.get("severity", "中"), 9))

    board_meta = dict(src.board_meta)
    board_meta["rule_set"] = f"{factory_label(factory)}-{board_type}-{level}"
    board_meta.setdefault("finish_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    board_meta.setdefault("submitter", "Evan DFM")
    board_meta["task_type"] = "DFM 分析"

    info_stats = dict(src.info_stats or {})
    extra_man = ctx.extras.get("manufacturing_extra") or {}
    if extra_man:
        merged = dict(info_stats.get("manufacturing") or {})
        for k, v in extra_man.items():
            if v not in (None, ""):
                merged.setdefault(k, v)
        info_stats["manufacturing"] = merged
    if ctx.extras.get("assembly_stats"):
        merged = dict(info_stats.get("assembly") or {})
        for k, v in ctx.extras["assembly_stats"].items():
            merged.setdefault(k, v)
        info_stats["assembly"] = merged
    if ctx.extras.get("capability_notes"):
        notes = dict(info_stats.get("capability_notes") or {})
        notes.update({k: v for k, v in ctx.extras["capability_notes"].items()
                      if v not in (None, "")})
        info_stats["capability_notes"] = notes

    critical = [it for it in issues if it.get("severity") == "危险"]
    high = [it for it in issues if it.get("severity") in ("高", "中")]

    result: Dict[str, Any] = {
        "factory": factory,
        "factory_label": factory_label(factory),
        "board_type": board_type,
        "board_type_label": load_capability_db(factory)["board_types"].get(
            board_type, {}).get("label", board_type),
        "capability_level": level,
        "capability_label": level_label(board_type, level, factory=factory),
        "copper_um": copper_um,
        "copper_oz": copper_oz,
        "board_meta": board_meta,
        "issues": issues,
        "instances": instances[:MAX_INSTANCES_IN_RESULT],
        "fab_data": src.fab_data,
        "issue_summary": summary,
        "risk": risk,
        "info_stats": info_stats,
        "design_params": src.params,
        "checks_run": checks_run,
        "checks_skipped": checks_skipped,
        "modules_run": checks_run,          # 兼容旧字段名
        "modules_requested": sorted(requested) if requested else [s.id for s in specs],
        "plugins": plugins_report,
        "rules_source": rules.source,
        "errors": [it["description"] for it in critical],
        "warnings": [it["description"] for it in high],
        "error_count": len(critical),
        "warning_count": len(high),
        "passed": len(critical) == 0,
        "source": {"type": src.kind, "path": src.path,
                   "fab_path": str(cfg.get("fab_path") or "")},
        "notes": list(src.warnings),
    }
    for key in ("netlist_summary", "fabnet_summary", "geometry_stats", "layer_stack",
                "copper_layers", "gerber_meta", "fab_stats", "capability_items",
                "capability_summary", "net_classes", "assembly_stats"):
        if ctx.extras.get(key) is not None:
            result[key] = ctx.extras[key]
    for k, v in errors.items():
        result[k] = v
    return result


def analyze_file(path: str, factory: str = "jlcpcb", board_type: str = "rigid",
                 capability_level: str = "standard", copper_um: float = 35.0,
                 copper_oz: Optional[float] = None,
                 board_thickness: Optional[float] = None,
                 fab_path: Optional[str] = None, cad_nets: Any = None,
                 design_params: Any = None, modules: Optional[List[str]] = None,
                 rules_path: Optional[str] = None,
                 progress: Optional[Callable[[str], None]] = None,
                 **extra: Any) -> Dict[str, Any]:
    """便捷入口: 直接对一个文件/目录做 DFM 分析。

    数据源类型自动识别 (.brd / .json / Gerber / ODB++ 目录)。
    """
    from dfm.core.source import SOURCE_GERBER, SOURCE_ODB, detect_source_type

    kind = detect_source_type(path)
    cfg: Dict[str, Any] = {
        "source_type": kind,
        "source_path": path,
        "fab_path": fab_path,
        "cad_nets": cad_nets,
        "design_params": design_params,
        "factory": factory,
        "board_type": board_type,
        "capability_level": capability_level,
        "copper_um": copper_um,
        "copper_oz": copper_oz,
        "board_thickness": board_thickness,
        "modules": modules,
        "rules_path": rules_path,
    }
    if kind in (SOURCE_GERBER, SOURCE_ODB) and not fab_path:
        cfg["fab_path"] = path
    cfg.update({k: v for k, v in extra.items() if v is not None})
    return analyze(cfg, progress=progress)


def run_hub(cfg: Dict[str, Any],
            progress: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """兼容旧名称, 等价于 :func:`analyze`。"""
    return analyze(cfg, progress=progress)
