"""命令行接口 (CI/自动化友好)。

::

    python -m dfm gui                                   # 启动桌面界面
    python -m dfm analyze board.brd -o out --format excel,html
    python -m dfm analyze gerber_dir --factory jlcpcb --level extreme --thickness 1.6
    python -m dfm info board.json                       # 只看解析结果与统计
    python -m dfm rules list|show|template|path         # 规则库管理
    python -m dfm checks                                # 列出全部检查项(含插件)
    python -m dfm skill-export                          # 导出 Allegro SKILL 脚本
    python -m dfm version
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

from dfm import __product__, __version__


def _print(msg: str = "") -> None:
    print(msg)


def _kv_block(title: str, data: Dict[str, Any]) -> None:
    if not data:
        return
    _print(f"  {title}")
    for k, v in data.items():
        if v in (None, ""):
            continue
        _print(f"    {k:<22} {v}")


# ============================================================
# analyze
# ============================================================
def cmd_analyze(args: argparse.Namespace) -> int:
    from dfm.core.pipeline import analyze
    from dfm.report import FORMATS, export_report

    if not args.source and not args.params:
        _print("[ERROR] 请指定数据源 (.brd / .json / Gerber 目录 / ODB++ 目录) 或 --params")
        return 2

    modules = None
    if args.modules:
        modules = [m.strip() for m in args.modules.split(",") if m.strip()]

    cfg: Dict[str, Any] = {
        "source_type": args.type,
        "source_path": args.source or "",
        "fab_path": args.fab,
        "cad_nets": args.cad_nets,
        "design_params": args.params,
        "factory": args.factory,
        "board_type": args.board_type,
        "capability_level": args.level,
        "copper_um": args.copper_um,
        "copper_oz": args.copper_oz,
        "board_thickness": args.thickness,
        "modules": modules,
        "rules_path": args.rules,
        "customer": args.customer,
        "submitter": args.submitter,
        "extracta": args.extracta,
    }
    if args.no_plugins:
        cfg["load_plugins"] = False

    quiet = args.quiet
    result = analyze(cfg, progress=None if quiet else (lambda m: _print("  " + m)))

    summary = result["issue_summary"]
    risk = result["risk"]
    if not args.json:
        _print("=" * 78)
        _print(f"  {__product__} v{__version__} — DFM 分析报告")
        _print("=" * 78)
        meta = result.get("board_meta") or {}
        _print(f"  板名: {meta.get('board_name', '')}   版本: {meta.get('version', '') or '-'}")
        _print(f"  规则集: {result['factory_label']} / {result['board_type_label']} / "
               f"{result['capability_label']} / 铜厚 {result['copper_um']}um")
        _print(f"  数据源: {result['source']['type']} {result['source']['path'] or '-'}")
        _print(f"  已执行检查: {', '.join(result.get('checks_run') or []) or '(无)'}")
        skipped = result.get("checks_skipped") or []
        if skipped:
            _print("  跳过检查: " + "; ".join(
                f"{s['id']}({s['reason']})" for s in skipped))
        _print("-" * 78)
        _print(f"  问题总数: {summary.get('总计', 0)}  (危险 {summary.get('危险', 0)} / "
               f"高 {summary.get('高', 0)} / 中 {summary.get('中', 0)} / "
               f"低 {summary.get('低', 0)})")
        _print(f"  可制造性评分: {risk.get('score')}/100   风险等级: {risk.get('risk_level')}")
        _print(f"  结论: {risk.get('verdict')}")
        _print("-" * 78)
        limit = args.limit or 0
        for i, it in enumerate(result["issues"], 1):
            if limit and i > limit:
                _print(f"  ... 其余 {len(result['issues']) - limit} 项见报告")
                break
            loc = f" @({it['x']:.2f},{it['y']:.2f})" if it.get("x") is not None else ""
            _print(f"  [{it['severity']}] {it['code']} {it.get('module', '')} "
                   f"{it['rule']} x{it.get('count', 1)}{loc}")
            if not args.brief:
                _print(f"        {it['description']}")
                if it.get("suggestion"):
                    _print(f"        建议: {it['suggestion']}")
        for n in result.get("notes") or []:
            _print(f"  [提示] {n}")
    else:
        from dfm.report import slim_analysis
        _print(json.dumps(slim_analysis(result), ensure_ascii=False, indent=2, default=str))

    if args.out and not args.json:
        formats = ([f.strip().lower() for f in args.format.split(",") if f.strip()]
                   if args.format else ["excel", "text"])
        bad = [f for f in formats if f not in FORMATS]
        if bad:
            _print(f"[ERROR] 未知报告格式: {', '.join(bad)} (可用: {', '.join(FORMATS)})")
            return 2
        paths = export_report(result, args.out, formats=formats,
                              embed_images=not args.no_images)
        _print("-" * 78)
        for fmt, p in paths.items():
            _print(f"  报告({fmt}): {p}")
    if args.view:
        _open_viewer(result)
    return 0 if result.get("passed", True) or not args.strict else 1


def _open_viewer(result: dict) -> None:
    from dfm.app import run_viewer

    run_viewer(result)


# ============================================================
# info
# ============================================================
def cmd_info(args: argparse.Namespace) -> int:
    from dfm.core.source import load_source

    src = load_source({"source_type": args.type, "source_path": args.source,
                       "fab_path": args.fab, "board_thickness": args.thickness,
                       "extracta": args.extracta},
                      progress=lambda m: _print("  " + m))
    _print("=" * 78)
    _print(f"  数据源解析: {src.kind} {src.path}")
    _print("=" * 78)
    for w in src.warnings:
        _print(f"  [提示] {w}")
    _kv_block("板信息", src.board_meta)
    _kv_block("制造工艺", (src.info_stats or {}).get("manufacturing") or {})
    _kv_block("装配工艺", (src.info_stats or {}).get("assembly") or {})
    if src.fab_stats:
        _kv_block("制造数据", {k: v for k, v in src.fab_stats.items() if k != "nets"})
    if src.design is not None:
        gm = src.extras.get("gerber_meta") or {}
        if gm:
            _kv_block("Gerber 层识别", {
                "铜层": ", ".join(gm.get("copper_layers") or []),
                "阻焊": ", ".join(gm.get("mask_layers") or []),
                "丝印": ", ".join(gm.get("silk_layers") or []),
                "外形": ", ".join(gm.get("profile_layers") or []),
                "钻孔数": gm.get("drill_count"),
                "网络来源": "X2 属性" if gm.get("has_x2_net") else "几何连通性派生",
            })
    _print("-" * 78)
    _print("  量测到的设计参数 (可直接用于制程能力比对):")
    for k, v in sorted(src.params.items()):
        if not k.startswith("_"):
            _print(f"    {k:<28} {v}")
    return 0


# ============================================================
# rules / checks / factories / skill-export
# ============================================================
def cmd_rules(args: argparse.Namespace) -> int:
    from dfm import paths
    from dfm.core.capability import capability_file, factory_labels, list_factories
    from dfm.core.rules_config import RulesConfig, default_config_path, template_text

    action = args.action
    if action == "path":
        _print("规则库搜索路径 (优先级从高到低):")
        for d in paths.rules_dirs():
            _print(f"  {d}")
        _print(f"用户规则集建议位置: {default_config_path()}")
        _print(f"插件目录: {', '.join(paths.plugins_dirs())}")
        return 0

    if action == "list":
        cfg = RulesConfig.load(args.file)
        _print(f"规则集: {cfg.source or '(内置默认)'}")
        _print("=" * 78)
        _print("  板厂制程能力库:")
        labels = factory_labels()
        for fid in list_factories():
            path = capability_file(fid) or ""
            levels = ""
            try:
                from dfm.core.capability import list_board_types, list_levels
                levels = "; ".join(f"{bt}[{','.join(list_levels(bt, factory=fid))}]"
                                   for bt in list_board_types(fid))
            except Exception:  # noqa: BLE001
                pass
            _print(f"    {fid:<12} {labels.get(fid, fid):<14} {levels}")
            _print(f"                 {path}")
        _print("-" * 78)
        _print("  检查开关:")
        for cid, on in (cfg.describe()["checks"]).items():
            _print(f"    {'[x]' if on else '[ ]'} {cid}")
        d = cfg.describe()
        _print(f"  规则覆盖: {d['rule_overrides']} 条   自定义规则: {d['custom_rules']} 条")
        return 0

    if action == "show":
        cfg = RulesConfig.load(args.file)
        import yaml
        _print(yaml.safe_dump(cfg.data, allow_unicode=True, sort_keys=False))
        return 0

    if action == "template":
        out = args.out or default_config_path()
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(template_text())
        _print(f"  规则集模板已写出: {out}")
        _print("  编辑后在界面点『重新加载规则库』或重跑命令即可生效。")
        return 0

    if action == "capability":
        from dfm.core.capability import capability_file
        path = capability_file(args.factory)
        if not path:
            _print(f"[ERROR] 未找到板厂能力库: {args.factory}")
            return 2
        _print(path)
        if args.open:
            os.startfile(path)  # noqa: S606
        return 0

    _print("[ERROR] 未知 rules 动作")
    return 2


def cmd_checks(args: argparse.Namespace) -> int:
    from dfm.core import registry

    registry.load_builtin_checks()
    if not args.no_plugins:
        registry.load_plugins()
    _print("=" * 78)
    _print(f"  已注册检查项 ({len(registry.list_checks())})")
    _print("=" * 78)
    for c in registry.list_checks():
        src = "内置" if c.source == "builtin" else os.path.basename(c.source)
        _print(f"  {c.id:<14} {c.name}")
        _print(f"    {'':<12} 分组={c.group}  顺序={c.order}  依赖={','.join(c.requires) or '-'}"
               f"  来源={src}")
        if c.description:
            _print(f"    {'':<12} {c.description}")
    plugins = registry.loaded_plugins()
    if plugins:
        _print("-" * 78)
        _print("  插件:")
        for path, checks in plugins.items():
            _print(f"    {path} -> {', '.join(checks) or '(未注册检查)'}")
    return 0


def cmd_skill_export(args: argparse.Namespace) -> int:
    from dfm.io.skill_export import EXPORT_SKILL_FILENAME, write_export_skill

    out = args.out or os.path.join(os.getcwd(), EXPORT_SKILL_FILENAME)
    path = write_export_skill(out)
    _print("=" * 78)
    _print("  Allegro SKILL 数据导出脚本已写出")
    _print("=" * 78)
    _print(f"  文件: {path}")
    _print("  用法: 在 Allegro PCB Editor 打开 .brd, Command 窗口执行:")
    _print(f'        skill load "{os.path.basename(path)}"')
    _print("        brdExportJSON            ; 导出到 .brd 同目录")
    _print('        或 (brdExportJSON "D:/out/brd_export.json")')
    _print("  然后:  python -m dfm analyze <export.json>")
    return 0


def cmd_version(_args: argparse.Namespace) -> int:
    from dfm import paths
    from dfm.core.capability import list_factories
    from dfm.core import registry

    registry.load_builtin_checks()
    _print(f"{__product__} v{__version__}")
    _print(f"  Python     {sys.version.split()[0]}")
    _print(f"  程序目录   {paths.app_root()}")
    _print(f"  用户数据   {paths.user_data_dir(create=False)}")
    _print(f"  板厂能力库 {len(list_factories())} 家: {', '.join(list_factories())}")
    _print(f"  内置检查   {len(registry.builtin_checks())} 项")
    for name, ok in (("PySide6", "PySide6"), ("openpyxl", "openpyxl"),
                     ("PyYAML", "yaml"), ("Pillow", "PIL")):
        try:
            __import__(ok)
            _print(f"  {name:<10} 已安装")
        except ImportError:
            _print(f"  {name:<10} 未安装")
    return 0


# ============================================================
# parser
# ============================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="evan-dfm",
        description=f"{__product__} v{__version__} — PCB 可制造性(DFM)专业分析",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m dfm gui
  python -m dfm analyze D:/board.brd -o D:/out --format excel,html
  python -m dfm analyze D:/gerber --factory 嘉立创 --level extreme --thickness 1.6
  python -m dfm analyze params.json --type params --modules capability,panelization
  python -m dfm info D:/board.json
  python -m dfm rules list
  python -m dfm checks
""")
    sub = p.add_subparsers(dest="command")

    g = sub.add_parser("gui", help="启动桌面图形界面")
    g.add_argument("--page", default=None, help="启动后直接打开的页面 id")
    g.set_defaults(func=cmd_gui)

    a = sub.add_parser("analyze", help="执行 DFM 分析并导出报告")
    a.add_argument("source", nargs="?", default="", help=".brd / .json / Gerber 目录 / ODB++ 目录")
    a.add_argument("--type", default=None,
                   help="数据源类型 brd|json|gerber|odb|params|none (默认自动识别)")
    a.add_argument("--fab", default=None, help="附加制造数据 (Gerber/ODB++/Drill)")
    a.add_argument("--cad-nets", dest="cad_nets", default=None, help="CAD 网表 JSON")
    a.add_argument("--params", default=None, help="设计参数 JSON (制程能力比对)")
    a.add_argument("--factory", default="jlcpcb", help="板厂 id / 中文名 / 别名")
    a.add_argument("--board-type", dest="board_type", default="rigid", help="rigid|flex")
    a.add_argument("--level", default="standard", help="能力等级 standard|automotive|extreme")
    a.add_argument("--copper-um", dest="copper_um", type=float, default=35.0, help="成品铜厚 um")
    a.add_argument("--copper-oz", dest="copper_oz", type=float, default=None, help="成品铜厚 oz")
    a.add_argument("--thickness", type=float, default=None, help="板厚 mm (Gerber 源必填才可判纵横比)")
    a.add_argument("--modules", default=None, help="逗号分隔的检查 id (默认全部)")
    a.add_argument("--rules", default=None, help="规则集 YAML 路径")
    a.add_argument("--no-plugins", dest="no_plugins", action="store_true", help="不加载插件")
    a.add_argument("--extracta", default=None, help="extracta.exe 路径 (BRD 源)")
    a.add_argument("--customer", default=None, help="报告: 客户名")
    a.add_argument("--submitter", default=None, help="报告: 提交人")
    a.add_argument("-o", "--out", default=None, help="输出文件或目录")
    a.add_argument("--format", default="excel,text", help="报告格式 excel,html,text,json,svg")
    a.add_argument("--no-images", dest="no_images", action="store_true", help="Excel 不内嵌截图")
    a.add_argument("--limit", type=int, default=0, help="控制台最多打印多少条问题 (0=全部)")
    a.add_argument("--brief", action="store_true", help="不打印描述与建议")
    a.add_argument("--json", action="store_true", help="以 JSON 输出完整结果到 stdout")
    a.add_argument("--quiet", action="store_true", help="不打印进度")
    a.add_argument("--strict", action="store_true", help="存在危险问题时返回码 1 (CI 用)")
    a.add_argument("--view", action="store_true", help="分析后打开版图交叉定位界面")
    a.set_defaults(func=cmd_analyze)

    i = sub.add_parser("info", help="只解析数据源并打印统计与量测参数")
    i.add_argument("source", help=".brd / .json / Gerber 目录 / ODB++ 目录")
    i.add_argument("--type", default=None)
    i.add_argument("--fab", default=None)
    i.add_argument("--thickness", type=float, default=None)
    i.add_argument("--extracta", default=None)
    i.set_defaults(func=cmd_info)

    r = sub.add_parser("rules", help="规则库管理")
    r.add_argument("action", choices=["list", "show", "template", "path", "capability"])
    r.add_argument("--file", default=None, help="规则集 YAML 路径")
    r.add_argument("--factory", default="jlcpcb", help="capability 动作: 板厂")
    r.add_argument("--out", default=None, help="template 动作: 输出路径")
    r.add_argument("--open", action="store_true", help="capability 动作: 用默认程序打开")
    r.set_defaults(func=cmd_rules)

    c = sub.add_parser("checks", help="列出全部检查项 (含插件)")
    c.add_argument("--no-plugins", dest="no_plugins", action="store_true")
    c.set_defaults(func=cmd_checks)

    s = sub.add_parser("skill-export", help="写出 Allegro SKILL 数据导出脚本")
    s.add_argument("out", nargs="?", default=None, help="输出路径 (.il)")
    s.set_defaults(func=cmd_skill_export)

    v = sub.add_parser("version", help="版本与环境信息")
    v.set_defaults(func=cmd_version)

    p.add_argument("--version", action="version", version=f"{__product__} {__version__}")
    return p


def cmd_gui(args: argparse.Namespace) -> int:
    from dfm.app import run_gui

    return run_gui(page=args.page)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        return run_default(parser)
    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        _print("\n[已取消]")
        return 130
    except Exception as exc:  # noqa: BLE001
        import traceback
        _print(f"[ERROR] {exc}")
        if os.environ.get("DFM_DEBUG"):
            traceback.print_exc()
        else:
            _print("        (设置环境变量 DFM_DEBUG=1 查看完整堆栈)")
        return 1


def run_default(parser: argparse.ArgumentParser) -> int:
    """无子命令时: 有图形环境则启动界面, 否则打印帮助。"""
    if sys.stdout.isatty() and os.environ.get("DISPLAY", "1") and _has_gui():
        return cmd_gui(argparse.Namespace(page=None))
    parser.print_help()
    return 0


def _has_gui() -> bool:
    try:
        import PySide6  # noqa: F401
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    sys.exit(main())
