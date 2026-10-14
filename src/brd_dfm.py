"""
BRD-AI: BRD DFM 分析编排 (SKILL 导出 JSON -> Valor 式报告)
=============================================================
串联:
  src.brd_geometry.load_brd_export   读取 SKILL 导出的几何/网表
  src.dfm_engine.load_capability     载入板厂制程能力
  src.dfm_geometry.analyze_geometry  几何 DFM (线宽/间距/孔环/孔径/板边/阻焊桥/DRC) -> 含XY实例
  src.dfm_netlist.analyze_netlist    开短路 + 网表一致性 -> 含XY实例
  aggregate_by_rule                  按规则汇总 (报告用, 每规则一行, 记录数量与定位点)
  dfm_rules.summarize/compute_risk   问题分级统计 + 风险评分

产出 analysis 字典, 与 dfm_report.generate_dfm_excel/generate_dfm_text 兼容, 并额外携带:
  instances        : 全部问题实例 (含XY, 供 fab_viewer 交叉定位)
  fab_data         : 由 to_fab_data 生成的版图数据 (供 fab_viewer 渲染)
  netlist_summary  : 开短路/网表一致性统计
  geometry_stats   : 几何统计
"""
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.brd_geometry import BrdDesign, load_brd_export, to_fab_data
from src.dfm_engine import (DEFAULT_FACTORY, factory_label, load_capability,
                            load_capability_db, normalize_factory, normalize_level,
                            level_label)
from src.dfm_geometry import analyze_geometry
from src.dfm_netlist import analyze_netlist
from src.dfm_rules import (CATEGORY_PREFIX, DEFAULT_PREFIX, SEVERITY_ORDER,
                           compute_risk_score, make_issue, summarize_issues)

MAX_VIEWER_INSTANCES = 8000


def aggregate_by_rule(instances: List[dict]) -> List[dict]:
    """把逐处问题实例按 (类别, 规则) 汇总为报告用问题 (每规则一条)"""
    groups: Dict[tuple, List[dict]] = {}
    order: List[tuple] = []
    for it in instances:
        key = (it.get("category", "DFM"), it.get("rule", ""))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(it)

    prefix_counter: Dict[str, int] = {}
    aggregated: List[dict] = []
    for key in order:
        category, rule = key
        grp = groups[key]
        # 严重度取组内最高
        sev = min(grp, key=lambda x: SEVERITY_ORDER.get(x.get("severity", "中"), 9))["severity"]
        prefix = CATEGORY_PREFIX.get(category, DEFAULT_PREFIX)
        if category == "网络比对":
            prefix = "NET" if rule in ("开路(Open)", "短路(Short)") else "NLC"
        elif category == "DRC":
            prefix = "DRC"
        prefix_counter[prefix] = prefix_counter.get(prefix, 0) + 1
        code = f"{prefix}-{prefix_counter[prefix]:02d}"

        # 代表定位点: 取第一个有 XY 的实例
        rep = next((g for g in grp if g.get("x") is not None), grp[0])
        locations = [[g.get("x"), g.get("y")] for g in grp
                     if g.get("x") is not None and g.get("y") is not None]
        n = len(grp)
        if n > 1:
            desc = f"{rule}: 共发现 {n} 处。示例 - {rep.get('description','')}"
        else:
            desc = rep.get("description", "")
        aggregated.append(make_issue(
            code=code, category=category, rule=rule, severity=sev,
            description=desc, suggestion=rep.get("suggestion", ""),
            measured=rep.get("measured"), required=rep.get("required"),
            unit=rep.get("unit", ""), layer=rep.get("layer", ""),
            from_=rep.get("from", ""), to=rep.get("to", ""),
            x=rep.get("x"), y=rep.get("y"), count=n, note=rep.get("note", ""),
            net=rep.get("net", ""),
        ))
        aggregated[-1]["locations"] = locations[:MAX_VIEWER_INSTANCES]
    aggregated.sort(key=lambda x: (SEVERITY_ORDER.get(x["severity"], 9), x["code"]))
    return aggregated


def _build_info_stats(design: BrdDesign) -> dict:
    st = design.stats()
    manufacturing = {
        "单板尺寸": f"{st['board_size'][0]} x {st['board_size'][1]}" if st["board_size"] else "",
        "单板厚度": st["board_thickness_mm"],
        "铜层数": st["layer_count"],
        "网络数": st["net_count"],
        "节点数": st["node_count"],
        "过孔数量": st["via_count"],
        "焊盘数量": st["pad_count"],
        "走线数量": st["cline_count"],
        "铺铜数量": st["shape_count"],
        "测试点数量": st["testpoint_count"],
        "Allegro DRC": st["drc_count"],
    }
    assembly = {
        "器件": st["component_count"],
    }
    return {
        "board_type": "rigid",
        "manufacturing": {k: v for k, v in manufacturing.items() if v not in (None, "")},
        "assembly": {k: v for k, v in assembly.items() if v not in (None, "")},
        "capability_notes": {},
    }


def analyze_brd_dfm(export_path: str, factory: str = DEFAULT_FACTORY,
                    board_type: str = "rigid", capability_level: str = "standard",
                    copper_um: float = 35.0, copper_oz: Optional[float] = None,
                    fab_path: Optional[str] = None) -> dict:
    """对 SKILL/extracta 导出的 JSON 执行完整 BRD DFM 分析"""
    design = load_brd_export(export_path)
    return _analyze(design, factory, board_type, capability_level,
                    copper_um, copper_oz, fab_path, source_path=export_path)


def analyze_brd_file(brd_path: str, factory: str = DEFAULT_FACTORY,
                     board_type: str = "rigid", capability_level: str = "standard",
                     copper_um: float = 35.0, copper_oz: Optional[float] = None,
                     fab_path: Optional[str] = None, workdir: Optional[str] = None,
                     json_out: Optional[str] = None,
                     extracta: Optional[str] = None) -> dict:
    """直接对 .brd 执行 DFM: 用 Cadence extracta **无界面**提取几何/网表/DRC 后分析。
    需要本机安装 Allegro (extracta.exe)。找不到时抛 RuntimeError, 调用方可回退 SKILL。
    """
    from src.brd_extracta import extract_brd
    dd = extract_brd(brd_path, workdir=workdir, json_out=json_out, extracta=extracta)
    design = BrdDesign(dd, source_path=brd_path)
    return _analyze(design, factory, board_type, capability_level,
                    copper_um, copper_oz, fab_path, source_path=brd_path)


def _analyze(design: BrdDesign, factory: str, board_type: str,
             capability_level: str, copper_um: float,
             copper_oz: Optional[float], fab_path: Optional[str],
             source_path: str = "") -> dict:
    factory = normalize_factory(factory)
    capability = load_capability(board_type, capability_level, factory=factory)
    level = normalize_level(capability_level)
    if copper_oz is None:
        copper_oz = max(1.0, round((copper_um or 35.0) / 35.0, 1))

    # 可选: 读取制造数据(Gerber/ODB++)网表, 做 CAD vs FAB 比对
    fab_nets = None
    fab_issues: List[dict] = []
    if fab_path:
        try:
            from src.fab_data_reader import analyze_fab, collect_fab_nets
            fab_result = analyze_fab(fab_path)
            fab_nets = collect_fab_nets(fab_result["data"])
            fab_issues = list(fab_result.get("issues", []))
        except Exception:  # noqa: BLE001
            fab_nets = None

    # 几何 DFM + 开短路/网表
    geom_instances = analyze_geometry(design, capability, copper_um=copper_um,
                                      copper_oz=copper_oz, board_type=board_type)
    net_instances, netlist_summary = analyze_netlist(design, fab_nets=fab_nets)

    instances = list(geom_instances) + list(net_instances) + list(fab_issues)
    issues = aggregate_by_rule(instances)

    summary = summarize_issues(issues)
    risk = compute_risk_score(issues)

    board_meta = {
        "customer": "",
        "board_name": design.board_name or os.path.basename(source_path),
        "version": design.revision or "",
        "assembly_name": os.path.basename(source_path),
        "rule_set": f"{factory_label(factory)}-{board_type}-{level}",
        "eda_tool": design.tool or "Cadence Allegro",
        "submit_time": "",
        "finish_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "submitter": "BRD-AI",
    }

    errors = [it for it in issues if it["severity"] in ("危险",)]
    warnings = [it for it in issues if it["severity"] in ("高", "中")]

    result = {
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
        "instances": instances[:MAX_VIEWER_INSTANCES],
        "issue_summary": summary,
        "risk": risk,
        "info_stats": _build_info_stats(design),
        "layer_stack": design.stackup,
        "copper_layers": design.copper_layers(),
        "netlist_summary": netlist_summary,
        "geometry_stats": design.stats(),
        "errors": [it["description"] for it in errors],
        "warnings": [it["description"] for it in warnings],
        "error_count": len(errors),
        "warning_count": len(warnings),
        "passed": len(errors) == 0,
        "fab_data": to_fab_data(design),
        "source_path": source_path,
    }
    return result
