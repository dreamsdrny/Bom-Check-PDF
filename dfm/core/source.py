"""数据源加载与归一化 — 把各种输入统一为一个可检查的设计模型。

支持的数据源 (``source_type``)::

    brd     Cadence Allegro .brd      需本机 extracta.exe (无界面提取)
    json    extracta / SKILL 导出 JSON 见 dfm.io.skill_export
    gerber  RS-274X + Excellon 钻孔    文件或目录
    odb     ODB++ 工程目录             含 matrix/matrix
    params  仅设计参数 JSON            无几何时做参数化能力比对
    none    无数据源                   仅参数化检查

无论哪种源, 最终都归一为:
  * :class:`dfm.io.brd_model.BrdDesign` — 几何/网表模型 (供几何与装配检查)
  * ``fab_data``  — 渲染用图层数据 (供版图交叉定位与报告内嵌图)
  * ``params``    — 设计参数 (供制程能力比对与自定义规则)
  * ``cad_nets``  — CAD 网表 (供 CAD vs FAB 比对)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from dfm.geom.measure import measure_design
from dfm.io.brd_model import BrdDesign, load_brd_export, to_fab_data

SOURCE_BRD = "brd"
SOURCE_JSON = "json"
SOURCE_GERBER = "gerber"
SOURCE_ODB = "odb"
SOURCE_PARAMS = "params"
SOURCE_NONE = "none"
SOURCE_TYPES = (SOURCE_BRD, SOURCE_JSON, SOURCE_GERBER, SOURCE_ODB,
                SOURCE_PARAMS, SOURCE_NONE)

SOURCE_LABELS = {
    SOURCE_BRD: "Cadence Allegro .brd (extracta 无界面提取)",
    SOURCE_JSON: "导出 JSON (extracta / SKILL)",
    SOURCE_GERBER: "Gerber (RS-274X) + NC Drill",
    SOURCE_ODB: "ODB++ 工程目录",
    SOURCE_PARAMS: "设计参数 JSON (无几何)",
    SOURCE_NONE: "无数据源 (仅参数化检查)",
}

GERBER_EXTS = (".gbr", ".ger", ".art", ".pho", ".gto", ".gbo", ".gts", ".gbs",
               ".gtl", ".gbl", ".gtp", ".gbp", ".gm1", ".gko")
DRILL_EXTS = (".drl", ".txt", ".exc", ".nc")


@dataclass
class SourceData:
    """一次分析的全部输入数据。"""

    kind: str = SOURCE_NONE
    path: str = ""
    design: Optional[BrdDesign] = None
    fab_data: List[dict] = field(default_factory=list)
    fab_stats: Dict[str, Any] = field(default_factory=dict)
    fab_issues: List[dict] = field(default_factory=list)
    fab_nets: Dict[str, Any] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)
    cad_nets: Dict[str, Any] = field(default_factory=dict)
    board_meta: Dict[str, Any] = field(default_factory=dict)
    info_stats: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    extras: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_design(self) -> bool:
        return self.design is not None

    @property
    def board_name(self) -> str:
        return str(self.board_meta.get("board_name") or "")


# ============================================================
# 数据源识别
# ============================================================
def detect_source_type(path: str) -> str:
    """按扩展名/目录结构推断数据源类型。"""
    if not path:
        return SOURCE_NONE
    p = str(path)
    low = p.lower()
    if os.path.isdir(p):
        if os.path.isfile(os.path.join(p, "matrix", "matrix")):
            return SOURCE_ODB
        return SOURCE_GERBER
    if low.endswith(".brd"):
        return SOURCE_BRD
    if low.endswith(".json"):
        return SOURCE_JSON
    if low.endswith(GERBER_EXTS) or low.endswith(DRILL_EXTS):
        return SOURCE_GERBER
    return SOURCE_PARAMS if low.endswith((".yaml", ".yml")) else SOURCE_NONE


def _load_json(path: str) -> Any:
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def _base_name(path: str) -> str:
    if not path:
        return ""
    p = str(path)
    if os.path.isdir(p):
        return os.path.basename(os.path.normpath(p))
    return os.path.splitext(os.path.basename(p))[0]


# ============================================================
# 参数派生: 从几何模型量出可比较的设计参数
# ============================================================
def derive_params(design: Optional[BrdDesign],
                  fab_stats: Optional[dict] = None,
                  rules=None) -> Dict[str, Any]:
    """从设计模型量测设计参数 (供制程能力比对)。

    只计算代价低的量 (线宽/孔径/孔环/板厚/层数/器件与焊盘统计);
    间距类由几何检查直接判定并定位, 不在此重复计算。

    :param rules: 规则集 (用于读取 ``assembly.fiducial_names`` 等配置,
                  保证量测口径与装配检查一致)
    """
    params: Dict[str, Any] = {}
    stats = fab_stats or {}
    if stats.get("min_line_width_mm") is not None:
        params["min_line_width"] = stats["min_line_width_mm"]
    if stats.get("min_hole_mm") is not None:
        params["min_mech_drill"] = stats["min_hole_mm"]
    if design is None:
        return params

    st = design.stats()
    widths = [c["width"] for c in design.clines if c.get("width")]
    if widths:
        params["min_line_width"] = round(min(widths), 4)

    drills = [v["drill_mm"] for v in design.vias if v.get("drill_mm")]
    drills += [p["drill_mm"] for p in design.pads if p.get("drill_mm")]
    if drills:
        params["min_mech_drill"] = round(min(drills), 4)
        params["max_mech_drill"] = round(max(drills), 4)
        params["pth_count"] = len(drills)

    rings = [round((v["pad_mm"] - v["drill_mm"]) / 2.0, 4)
             for v in design.vias
             if v.get("pad_mm") and v.get("drill_mm") and v["pad_mm"] > v["drill_mm"]]
    if rings:
        params["min_annular_ring"] = min(rings)

    laser = [v["drill_mm"] for v in design.vias
             if v.get("drill_mm") and str(v.get("type", "")).lower()
             in ("microvia", "laser", "blind")]
    if laser:
        params["min_laser_via"] = round(min(laser), 4)
        params["has_microvia"] = True
        params["has_blind_via"] = any(
            str(v.get("type", "")).lower() == "blind" for v in design.vias)

    if design.thickness_mm:
        params["board_thickness"] = design.thickness_mm
    if st.get("board_size"):
        params["board_size"] = st["board_size"]
    params["layer_count"] = st.get("layer_count")
    params["via_count"] = st.get("via_count")

    tps = [t["pad_mm"] for t in design.testpoints if t.get("pad_mm")]
    if tps:
        params["test_pad_diameter"] = round(min(tps), 4)

    comps = design.components or []
    if comps:
        params["comp_count"] = len(comps)
        sides = {str(c.get("side", "")).lower() for c in comps}
        params["smd_count"] = sum(1 for p in design.pads if p.get("is_smd"))
        params["tht_count"] = max(0, len(design.pads) - params["smd_count"])
        params["pin_count"] = len(design.pads)
        params["_sides"] = sorted(s for s in sides if s)

    # 装配类量测: 与 dfm.checks.assembly **共用同一实现**, 保证 Info 页数值
    # 与问题清单结论一致 (旧实现两处各写一套, 结果互相矛盾)
    fid_names = None
    if rules is not None:
        try:
            fid_names = rules.param("assembly", "fiducial_names", None)
        except Exception:
            fid_names = None
    params.update(measure_design(design, fid_names))
    return {k: v for k, v in params.items() if v is not None}


# ============================================================
# Info 统计 (报告 Info 页)
# ============================================================
def build_info_stats(design: Optional[BrdDesign], params: Dict[str, Any],
                     fab_stats: Dict[str, Any], board_type: str = "rigid",
                     capability_notes: Optional[dict] = None) -> Dict[str, Any]:
    manufacturing: Dict[str, Any] = {}
    assembly: Dict[str, Any] = {}

    if fab_stats:
        manufacturing.update({
            "Gerber图层数": len(fab_stats.get("gerber_layers") or []),
            "钻孔文件数": len(fab_stats.get("drill_files") or []),
            "ODB++工程数": len(fab_stats.get("odb_jobs") or []),
            "图形总数": fab_stats.get("feature_count"),
            "孔总数": fab_stats.get("hole_count"),
        })
    if design is not None:
        st = design.stats()
        manufacturing.update({
            "单板尺寸": (f"{st['board_size'][0]} x {st['board_size'][1]}"
                        if st.get("board_size") else ""),
            "单板厚度": st.get("board_thickness_mm"),
            "铜层数": st.get("layer_count"),
            "网络数": st.get("net_count"),
            "节点数": st.get("node_count"),
            "过孔数量": st.get("via_count"),
            "焊盘数量": st.get("pad_count"),
            "走线数量": st.get("cline_count"),
            "铺铜数量": st.get("shape_count"),
            "测试点数量": st.get("testpoint_count"),
            "Allegro DRC": st.get("drc_count"),
        })
        assembly.update({"器件": st.get("component_count")})
    for key, label in (("min_line_width", "实测最小线宽(mm)"),
                       ("min_mech_drill", "实测最小孔径(mm)"),
                       ("min_annular_ring", "实测最小单边孔环(mm)"),
                       ("min_laser_via", "实测最小激光孔(mm)"),
                       ("via_count", "过孔数量"),
                       ("pth_count", "金属化孔数量"),
                       ("comp_count", "器件数"),
                       ("smd_count", "表贴焊盘数"),
                       ("pin_count", "引脚数"),
                       ("fiducial_count_top", "A面基准点"),
                       ("fiducial_count_bottom", "B面基准点")):
        if params.get(key) is not None:
            (assembly if key.startswith(("comp_", "smd_", "pin_", "fiducial_"))
             else manufacturing).setdefault(label, params[key])

    manufacturing = {k: v for k, v in manufacturing.items() if v not in (None, "")}
    assembly = {k: v for k, v in assembly.items() if v not in (None, "")}
    return {
        "board_type": board_type,
        "manufacturing": manufacturing,
        "assembly": assembly,
        "capability_notes": capability_notes or {},
    }


# ============================================================
# 主入口
# ============================================================
def load_source(cfg: Dict[str, Any],
                progress: Optional[Callable[[str], None]] = None) -> SourceData:
    """按配置加载并归一化数据源。

    cfg 关键字段: ``source_type`` / ``source_path`` / ``fab_path`` /
    ``cad_nets`` / ``design_params`` / ``board_thickness`` / ``extracta`` /
    ``workdir`` / ``json_out`` / ``rules_path``。
    """
    def note(msg: str) -> None:
        if progress:
            progress(msg)

    # 规则集: 量测口径 (如基准点位号关键字) 需与检查保持一致
    rules = None
    try:
        from dfm.core.rules_config import RulesConfig

        rules = RulesConfig.load(cfg.get("rules_path"))
    except Exception:
        rules = None

    src = SourceData()
    path = str(cfg.get("source_path") or "")
    fab_path = cfg.get("fab_path") or ""
    kind = str(cfg.get("source_type") or "").lower().strip()
    thickness = cfg.get("board_thickness")

    if not kind or kind not in SOURCE_TYPES:
        kind = detect_source_type(path or (fab_path if isinstance(fab_path, str) else ""))
    src.kind = kind
    src.path = path

    # ---- 设计参数 / CAD 网表 ----
    dp = cfg.get("design_params")
    if isinstance(dp, str) and dp:
        try:
            loaded = _load_json(dp)
            if isinstance(loaded, dict):
                src.params.update(loaded)
        except Exception as exc:
            src.warnings.append(f"设计参数 JSON 读取失败: {exc}")
    elif isinstance(dp, dict):
        src.params.update(dp)

    cad = cfg.get("cad_nets")
    if isinstance(cad, str) and cad:
        try:
            from dfm.io.fab import normalize_cad_nets
            src.cad_nets = normalize_cad_nets(_load_json(cad))
        except Exception as exc:
            src.warnings.append(f"CAD 网表 JSON 读取失败: {exc}")
    elif isinstance(cad, dict):
        from dfm.io.fab import normalize_cad_nets
        src.cad_nets = normalize_cad_nets(cad)

    # ---- 几何源 ----
    try:
        if kind == SOURCE_BRD and path:
            note(f"解析 BRD (extracta 无界面提取): {_base_name(path)} ...")
            from dfm.io.extracta import extract_brd
            dd = extract_brd(path, workdir=cfg.get("workdir"),
                             json_out=cfg.get("json_out"),
                             extracta=cfg.get("extracta"))
            src.design = BrdDesign(dd, source_path=path)
            src.fab_data = to_fab_data(src.design)
        elif kind == SOURCE_JSON and path:
            note(f"解析导出 JSON: {_base_name(path)} ...")
            src.design = load_brd_export(path)
            src.fab_data = to_fab_data(src.design)
        elif kind in (SOURCE_GERBER, SOURCE_ODB):
            target = path or fab_path
            if not target:
                raise RuntimeError("未指定 Gerber / ODB++ 路径")
            note(f"解析制造数据: {_base_name(str(target))} ...")
            _load_fab_into(src, target, thickness, note)
    except Exception as exc:
        src.warnings.append(f"{kind} 数据源解析失败: {exc}")
        src.extras["source_error"] = str(exc)

    # ---- 附加制造数据 (BRD/JSON 源时用于 CAD vs FAB 比对) ----
    if fab_path and not src.fab_data and kind not in (SOURCE_GERBER, SOURCE_ODB):
        try:
            note(f"解析制造数据: {_base_name(str(fab_path))} ...")
            _load_fab_into(src, fab_path, thickness, note, keep_design=False)
        except Exception as exc:
            src.warnings.append(f"制造数据解析失败: {exc}")

    # ---- 派生参数 (人工填写的板厚/参数优先于自动量测) ----
    derived = derive_params(src.design, src.fab_stats, rules)
    if thickness is not None:
        derived["board_thickness"] = float(thickness)
        if src.design is not None and src.design.thickness_mm is None:
            src.design.thickness_mm = float(thickness)
    merged = dict(derived)
    merged.update({k: v for k, v in src.params.items() if v not in (None, "")})
    src.params = merged

    # ---- 元数据 ----
    name = ""
    if src.design is not None:
        name = src.design.board_name or _base_name(path)
    src.board_meta = {
        "customer": str(cfg.get("customer") or ""),
        "board_name": name or _base_name(path or str(fab_path)) or "Untitled",
        "version": (src.design.revision if src.design is not None else "") or str(cfg.get("revision") or ""),
        "assembly_name": name or _base_name(path or str(fab_path)),
        "eda_tool": (src.design.tool if src.design is not None else "") or _tool_label(kind),
        "rule_set": "",
        "submit_time": str(cfg.get("submit_time") or ""),
        "finish_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "submitter": str(cfg.get("submitter") or "Evan DFM"),
        "source_type": kind,
        "source_path": path or str(fab_path or ""),
    }
    src.info_stats = build_info_stats(src.design, src.params, src.fab_stats,
                                      board_type=str(cfg.get("board_type") or "rigid"))
    if src.design is not None:
        src.extras["geometry_stats"] = src.design.stats()
        src.extras["layer_stack"] = src.design.stackup
        src.extras["copper_layers"] = src.design.copper_layers()
    return src


def _load_fab_into(src: SourceData, target: Any, thickness: Any,
                   note: Callable[[str], None], keep_design: bool = True) -> None:
    """解析 Gerber/ODB++/Drill 到 src (fab_data + 统计 + 完整性问题 + 设计模型)"""
    from dfm.io.fab import collect_fab_nets, integrity_check, read_fab_data, summarize_fab

    fab = read_fab_data(target)
    if not fab:
        raise RuntimeError(f"未解析到制造数据: {target}")
    src.fab_data = fab
    src.fab_stats = summarize_fab(fab)
    src.fab_issues = integrity_check(fab)
    src.fab_nets = collect_fab_nets(fab)

    if not keep_design:
        return
    from dfm.io.gerber_model import build_design_dict

    name = _base_name(str(target[0] if isinstance(target, (list, tuple)) and target else target))
    dd = build_design_dict(fab, board_thickness=thickness, board_name=name or "Gerber")
    src.design = BrdDesign(dd, source_path=name)
    meta = dd.get("gerber_meta") or {}
    src.extras["gerber_meta"] = meta
    src.info_stats = {}
    src.extras.setdefault("manufacturing_extra", {}).update({
        "铜层数": len(meta.get("copper_layers") or []),
        "阻焊层": len(meta.get("mask_layers") or []),
        "丝印层": len(meta.get("silk_layers") or []),
        "钻孔数": meta.get("drill_count"),
        "网络来源": "X2 属性" if meta.get("has_x2_net") else "几何连通性派生",
    })
    if not meta.get("has_x2_net"):
        src.warnings.append(
            "Gerber 无 X2 网络属性(%TO.N/%TO.P): 网络由几何连通性派生, 可做间距/孔环/板边检查, "
            "但无法判定开路与短路; 建议导出 X2 或提供 CAD 网表")


def _tool_label(kind: str) -> str:
    return {
        SOURCE_BRD: "Cadence Allegro",
        SOURCE_JSON: "Cadence Allegro (SKILL/extracta 导出)",
        SOURCE_GERBER: "Gerber/ODB++/NC Drill",
        SOURCE_ODB: "ODB++",
        SOURCE_PARAMS: "设计参数",
    }.get(kind, "Evan DFM")
