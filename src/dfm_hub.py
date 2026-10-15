"""
DFM 检查中心: 统一分析编排框架 (多级检查)
=================================================
把分散的 DFM 能力统一到一条流水线, 输出**单一** analysis 结构
(与 src.dfm_report.generate_dfm_excel / generate_dfm_text 完全兼容),
并携带 instances / fab_data 以支持图形化交叉定位。

检查模块 (可勾选组合):
  capability  制程能力 DFM     设计参数 vs 板厂能力库 (dfm_engine.analyze_design)
  geometry    几何 DFM         线宽/线距/孔环/孔径/纵横比/板边/阻焊桥/DRC, 定位 XY
  netlist     开短路 + 网表一致性 (dfm_netlist)
  fab         制造数据完整性   Gerber / ODB++ / NC Drill (fab_data_reader)
  fabnet      CAD vs FAB 网络比对 (Open / Short)
  panel       拼版/工艺边 DFM  (panel_engine)

数据源 (三选一, 可叠加制造数据):
  brd    Cadence .brd  (需本机 Allegro extracta.exe, 无界面提取)
  json   extracta / SKILL 导出的 JSON
  none   仅参数化检查 (capability / panel)
"""
import os
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from src.dfm_rules import (CATEGORY_PREFIX, DEFAULT_PREFIX, SEVERITY_ORDER,
                           compute_risk_score, summarize_issues)

CHECK_MODULES = [
    ("capability", "制程能力 DFM (设计参数 vs 板厂能力库)"),
    ("geometry", "几何 DFM (线宽/线距/孔环/孔径/板边/阻焊桥/DRC, 定位XY)"),
    ("netlist", "开短路 + 网表一致性"),
    ("fab", "制造数据完整性 (Gerber / ODB++ / NC Drill)"),
    ("fabnet", "CAD vs FAB 网络比对 (Open / Short)"),
    ("panel", "拼版 / 工艺边 DFM"),
]

GEOMETRY_CATEGORIES = {"线路", "钻孔", "阻焊", "丝印", "塞孔", "覆盖膜", "测试",
                       "压合", "板厚", "表面处理", "DRC", "锣型", "补强"}
NETLIST_CATEGORIES = {"网络比对", "开短路", "网表", "网表一致性"}


def _recodes(issues: List[dict]) -> List[dict]:
    """统一重编问题代码, 避免多模块合并后编号冲突"""
    counter: Dict[str, int] = {}
    for it in issues:
        cat = it.get("category", "DFM")
        if cat in NETLIST_CATEGORIES:
            prefix = "NET" if it.get("rule") in ("开路(Open)", "短路(Short)") else "NLC"
        elif cat == "DRC":
            prefix = "DRC"
        elif cat == "制造数据":
            prefix = "FAB"
        elif cat == "拼板":
            prefix = "PNL"
        else:
            prefix = CATEGORY_PREFIX.get(cat, DEFAULT_PREFIX)
        counter[prefix] = counter.get(prefix, 0) + 1
        it["code"] = f"{prefix}-{counter[prefix]:02d}"
    issues.sort(key=lambda x: (SEVERITY_ORDER.get(x.get("severity", "中"), 9), x.get("code", "")))
    return issues


def _load_json(path: str) -> Any:
    import json
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def run_hub(cfg: dict, progress: Optional[Callable[[str], None]] = None) -> dict:
    """执行 DFM 检查中心分析。

    cfg:
      source_type: "brd" | "json" | "none"
      source_path: .brd 或导出 JSON 路径
      design_params: dict 或 JSON 路径 (capability 模块)
      fab_path:      Gerber/ODB++/Drill 目录或文件
      cad_nets:      CAD 网表 JSON 路径或 dict (fabnet 模块)
      panel_params:  dict 或 JSON 路径 (panel 模块)
      factory / board_type / capability_level / copper_um / copper_oz
      modules:       启用的模块 id 列表
      extracta:      extracta.exe 路径 (可选)
    """
    def note(msg: str):
        if progress:
            progress(msg)

    modules = set(cfg.get("modules") or [m[0] for m in CHECK_MODULES])
    factory = cfg.get("factory") or "hongban"
    board_type = cfg.get("board_type") or "rigid"
    level = cfg.get("capability_level") or "standard"
    copper_um = float(cfg.get("copper_um") or 35.0)
    copper_oz = cfg.get("copper_oz")
    if copper_oz is None:
        copper_oz = max(1.0, round(copper_um / 35.0, 1))

    issues: List[dict] = []
    instances: List[dict] = []
    fab_data: List[dict] = []
    info_stats: Dict[str, Any] = {"manufacturing": {}, "assembly": {}, "capability_notes": {}}
    board_meta: Dict[str, str] = {}
    extra: Dict[str, Any] = {}
    modules_run: List[str] = []
    base = None

    # ---------- 1) BRD / JSON 源: 几何 DFM + 开短路 + 网表 ----------
    src_type = (cfg.get("source_type") or "none").lower()
    src_path = cfg.get("source_path") or ""
    def absorb(b, tag):
        """把一次完整分析结果(BRD 或 Gerber)合并进 Hub"""
        nonlocal instances, fab_data, board_meta, info_stats
        modules_run.append(tag)
        keep_geom = "geometry" in modules
        keep_net = "netlist" in modules
        for it in b.get("issues", []):
            cat = it.get("category", "")
            is_net = cat in NETLIST_CATEGORIES
            if (is_net and keep_net) or ((not is_net) and keep_geom):
                it = dict(it)
                it.setdefault("module", tag)
                issues.append(it)
        instances = list(b.get("instances", []))
        fab_data = b.get("fab_data") or []
        board_meta = dict(b.get("board_meta") or {})
        if b.get("info_stats"):
            info_stats = dict(b["info_stats"])
        extra["netlist_summary"] = b.get("netlist_summary")
        extra["geometry_stats"] = b.get("geometry_stats")
        extra["layer_stack"] = b.get("layer_stack")
        if b.get("gerber_meta"):
            extra["gerber_meta"] = b["gerber_meta"]
        for w in (b.get("warnings") or []):
            extra.setdefault("notes", []).append(w)
        if keep_geom:
            modules_run.append("geometry")
        if keep_net:
            modules_run.append("netlist")

    want_geo = bool(modules & {"geometry", "netlist"})

    # ---------- 1a) BRD / 导出 JSON 源 ----------
    if src_path and src_type in ("brd", "json") and want_geo:
        note(f"[1/5] 解析 {os.path.basename(src_path)} ...")
        try:
            from src.brd_dfm import analyze_brd_dfm, analyze_brd_file
            if src_type == "brd":
                base = analyze_brd_file(src_path, factory=factory, board_type=board_type,
                                        capability_level=level, copper_um=copper_um,
                                        copper_oz=copper_oz, fab_path=cfg.get("fab_path"),
                                        extracta=cfg.get("extracta"))
            else:
                base = analyze_brd_dfm(src_path, factory=factory, board_type=board_type,
                                       capability_level=level, copper_um=copper_um,
                                       copper_oz=copper_oz, fab_path=cfg.get("fab_path"))
            absorb(base, "brd")
        except Exception as exc:  # noqa: BLE001
            extra["brd_error"] = str(exc)
            note(f"    BRD 分析失败: {exc}")

    # ---------- 1b) Gerber / ODB++ / NC Drill 源 (转成同一设计模型) ----------
    gsrc = None
    if src_type in ("gerber", "odb", "fab"):
        gsrc = src_path or cfg.get("fab_path")
    elif want_geo and base is None and cfg.get("fab_path"):
        gsrc = cfg.get("fab_path")
    if gsrc and want_geo and base is None:
        note(f"[1/5] 解析制造数据几何并做 DFM: {os.path.basename(str(gsrc))} ...")
        try:
            from src.gerber_design import analyze_gerber_dfm
            gres = analyze_gerber_dfm(gsrc, factory=factory, board_type=board_type,
                                      capability_level=level, copper_um=copper_um,
                                      copper_oz=copper_oz,
                                      board_thickness=cfg.get("board_thickness"),
                                      cad_nets=cfg.get("cad_nets"))
            absorb(gres, "gerber")
            base = gres
            if gres.get("fabnet_summary"):
                extra["fabnet_summary"] = gres["fabnet_summary"]
                if "fabnet" in modules:
                    modules_run.append("fabnet")
        except Exception as exc:  # noqa: BLE001
            extra["gerber_error"] = str(exc)
            note(f"    Gerber 几何 DFM 失败: {exc}")

    # ---------- 2) 制程能力 DFM (参数化) ----------
    if "capability" in modules:
        dp = cfg.get("design_params")
        if isinstance(dp, str) and dp:
            try:
                dp = _load_json(dp)
            except Exception as exc:  # noqa: BLE001
                dp = None
                extra["design_params_error"] = str(exc)
        if isinstance(dp, dict) and dp:
            note("[2/5] 制程能力 DFM (设计参数 vs 能力库) ...")
            try:
                from src.dfm_engine import analyze_design
                cap = analyze_design(dp, board_type=board_type, capability_level=level,
                                     factory=factory)
                for it in cap.get("issues", []):
                    it = dict(it)
                    it["module"] = "capability"
                    issues.append(it)
                modules_run.append("capability")
                extra["capability_items"] = cap.get("items")
                if cap.get("info_stats"):
                    for k, v in (cap["info_stats"].get("manufacturing") or {}).items():
                        info_stats.setdefault("manufacturing", {}).setdefault(k, v)
            except Exception as exc:  # noqa: BLE001
                extra["capability_error"] = str(exc)

    # ---------- 3) 制造数据完整性 ----------
    fab_path = cfg.get("fab_path")
    fab_result = None
    if fab_path and ("fab" in modules or "fabnet" in modules):
        note("[3/5] 解析制造数据 (Gerber / ODB++ / Drill) ...")
        try:
            from src.fab_data_reader import analyze_fab
            fab_result = analyze_fab(fab_path)
            if "fab" in modules:
                for it in fab_result.get("issues", []):
                    it = dict(it)
                    it["module"] = "fab"
                    issues.append(it)
                modules_run.append("fab")
            if not fab_data:
                fab_data = fab_result.get("data") or []
            extra["fab_stats"] = {k: v for k, v in (fab_result.get("stats") or {}).items()
                                  if k != "nets"}
            st = fab_result.get("stats") or {}
            man = info_stats.setdefault("manufacturing", {})
            man.setdefault("Gerber图层数", len(st.get("gerber_layers", [])))
            man.setdefault("钻孔文件数", len(st.get("drill_files", [])))
            man.setdefault("图形总数", st.get("feature_count"))
            man.setdefault("孔总数", st.get("hole_count"))
        except Exception as exc:  # noqa: BLE001
            extra["fab_error"] = str(exc)

    # ---------- 4) CAD vs FAB 网络比对 ----------
    if "fabnet" in modules and fab_result is not None:
        note("[4/5] CAD vs FAB 网络比对 ...")
        try:
            from src.fab_data_reader import (collect_fab_nets, compare_netlists,
                                             netlist_compare_issues, normalize_cad_nets)
            fab_nets = collect_fab_nets(fab_result.get("data") or [])
            cad = cfg.get("cad_nets")
            if isinstance(cad, str) and cad:
                cad = normalize_cad_nets(_load_json(cad))
            elif isinstance(cad, dict):
                cad = normalize_cad_nets(cad)
            else:
                cad = {}
            if cad:
                cmp = compare_netlists(cad, fab_nets)
                for it in netlist_compare_issues(cmp):
                    it = dict(it)
                    it["module"] = "fabnet"
                    issues.append(it)
                extra["fabnet_summary"] = {k: v for k, v in cmp.items()
                                           if k not in ("node_open", "node_short")}
                modules_run.append("fabnet")
            else:
                extra["fabnet_summary"] = {"note": "未提供 CAD 网表, 跳过比对",
                                           "fab_net_count": len(fab_nets)}
        except Exception as exc:  # noqa: BLE001
            extra["fabnet_error"] = str(exc)

    # ---------- 5) 拼版 / 工艺边 DFM ----------
    if "panel" in modules:
        pp = cfg.get("panel_params")
        if isinstance(pp, str) and pp:
            try:
                pp = _load_json(pp)
            except Exception:
                pp = None
        if isinstance(pp, dict) and pp:
            note("[5/5] 拼版 / 工艺边 DFM ...")
            try:
                from src.panel_engine import design_panel
                plan = design_panel(pp)
                for it in plan.get("issues", []):
                    it = dict(it)
                    it["module"] = "panel"
                    issues.append(it)
                modules_run.append("panel")
                extra["panel"] = {"ok": plan.get("ok"),
                                  "utilization": plan.get("utilization"),
                                  "counts": plan.get("counts"),
                                  "cost": plan.get("cost"),
                                  "recommendation": plan.get("recommendation")}
            except Exception as exc:  # noqa: BLE001
                extra["panel_error"] = str(exc)

    # ---------- 汇总 ----------
    note("汇总问题与评分 ...")
    issues = _recodes(issues)
    summary = summarize_issues(issues)
    risk = compute_risk_score(issues)

    from src.dfm_engine import (factory_label, level_label, load_capability_db,
                                normalize_factory, normalize_level)
    factory = normalize_factory(factory)
    errors = [it for it in issues if it.get("severity") == "危险"]
    warnings = [it for it in issues if it.get("severity") in ("高", "中")]
    board_meta.setdefault("finish_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    board_meta.setdefault("submitter", "DFM-Hub")
    board_meta.setdefault("rule_set", f"{factory_label(factory)}-{board_type}-{normalize_level(level)}")
    board_meta.setdefault("task_type", "DFM 检查中心 (多模块)")

    return {
        "factory": factory,
        "factory_label": factory_label(factory),
        "board_type": board_type,
        "board_type_label": load_capability_db(factory)["board_types"].get(
            board_type, {}).get("label", board_type),
        "capability_level": normalize_level(level),
        "capability_label": level_label(board_type, normalize_level(level), factory=factory),
        "copper_um": copper_um,
        "copper_oz": copper_oz,
        "board_meta": board_meta,
        "issues": issues,
        "instances": instances,
        "fab_data": fab_data,
        "issue_summary": summary,
        "risk": risk,
        "info_stats": info_stats,
        "modules_run": modules_run,
        "modules_requested": sorted(modules),
        "errors": [it["description"] for it in errors],
        "warnings": [it["description"] for it in warnings],
        "error_count": len(errors),
        "warning_count": len(warnings),
        "passed": len(errors) == 0,
        "source": {"type": src_type, "path": src_path, "fab_path": fab_path or ""},
        **extra,
    }