"""制程能力库 (Capability Database) — DFM 判定的规则来源。

能力库为 YAML 文件, 存放于 `rules/capability/<板厂id>.yaml`, 由本模块**自动发现**:
新增一个 YAML 文件即可新增一家板厂, 无需改动任何代码 (二次开发友好)。

目录搜索优先级见 :mod:`dfm.paths`::

    $EVAN_DFM_RULES/capability  >  %APPDATA%/EvanDFM/rules/capability  >  <程序目录>/rules/capability

高优先级目录中的同名文件会覆盖低优先级 (用户可复制一份出厂能力库到用户目录后自行修改)。

能力库结构 (每个 board_type 下按 level 分档)::

    meta: {factory: 显示名, revision: 版本, aliases: [别名...]}
    board_types:
      rigid:
        label: 硬板
        levels: [standard, automotive, extreme]
        level_labels: {standard: 常规, ...}
        standard:
          panel: {...}  trace: {...}  drill: {...}  solder_mask: {...}
          silkscreen: {...}  plug_hole: {...}  plating: {...}  lamination: {...}
          test: {...}  surface_finish: {...}  coverlay: {...}

单位约定: 每项均带 unit 字段 (mm / um / mil / percent / ratio),
读取时统一用 :func:`_to_mm` 换算为 mm。
"""
import os
from typing import Any, Dict, List, Optional, Tuple

import yaml

from dfm import paths

MIL_TO_MM = 0.0254

DEFAULT_FACTORY = "hongban"

LEVEL_ALIASES = {
    "advanced": "extreme",      # 旧级别兼容
    "extreme": "extreme",
    "standard": "standard",
    "automotive": "automotive",
    "常规": "standard",
    "车规": "automotive",
    "极限": "extreme",
}

_DB_CACHE: Dict[str, dict] = {}
_DB_CACHE_MTIME: Dict[str, Optional[float]] = {}
_FILE_CACHE: Dict[str, str] = {}
_FILE_CACHE_STAMP: List[float] = [0.0]


def clear_capability_cache() -> None:
    """清空能力库缓存 (修改/新增 rules/capability/*.yaml 后调用以强制重载)"""
    _DB_CACHE.clear()
    _DB_CACHE_MTIME.clear()
    _FILE_CACHE.clear()
    _FILE_CACHE_STAMP[0] = -1.0


def _dir_stamp() -> float:
    stamp = 0.0
    for d in paths.capability_dirs():
        try:
            stamp += os.path.getmtime(d)
        except OSError:
            pass
    return stamp


def _capability_files(rescan: bool = False) -> Dict[str, str]:
    """扫描全部能力库文件, 返回 {板厂id: 绝对路径} (高优先级目录覆盖同名文件)"""
    stamp = _dir_stamp()
    if _FILE_CACHE and not rescan and _FILE_CACHE_STAMP[0] == stamp:
        return dict(_FILE_CACHE)
    found: Dict[str, str] = {}
    for d in reversed(paths.capability_dirs()):
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.lower().endswith((".yaml", ".yml")):
                found[os.path.splitext(fn)[0]] = os.path.join(d, fn)
    _FILE_CACHE.clear()
    _FILE_CACHE.update(found)
    _FILE_CACHE_STAMP[0] = stamp
    return dict(found)


def capability_file(factory: str) -> Optional[str]:
    """某板厂能力库 YAML 的实际路径 (用于界面『打开规则库』)"""
    fid = normalize_factory(factory)
    return _capability_files().get(fid)


def _load_db(factory_id: str) -> dict:
    """按 id 直接加载 (不做别名归一, 避免递归)"""
    files = _capability_files()
    path = files.get(factory_id)
    if not path:
        path = files.get(DEFAULT_FACTORY)
        if not path:
            raise FileNotFoundError(
                "未找到任何制程能力库, 请检查 rules/capability/*.yaml "
                f"(搜索路径: {paths.capability_dirs()})")
    try:
        mtime: Optional[float] = os.path.getmtime(path)
    except OSError:
        mtime = None
    cached = _DB_CACHE.get(factory_id)
    if cached is not None and _DB_CACHE_MTIME.get(factory_id) == mtime:
        return cached
    with open(path, "r", encoding="utf-8") as f:
        db = yaml.safe_load(f) or {}
    db.setdefault("_source_file", path)
    _DB_CACHE[factory_id] = db
    _DB_CACHE_MTIME[factory_id] = mtime
    return db


def list_factories() -> List[str]:
    """全部可用板厂 id (默认板厂排首位, 便于界面下拉框)"""
    ids = sorted(_capability_files().keys())
    if DEFAULT_FACTORY in ids:
        ids.remove(DEFAULT_FACTORY)
        ids.insert(0, DEFAULT_FACTORY)
    return ids


def factory_labels() -> Dict[str, str]:
    """{板厂id: 显示名}"""
    out: Dict[str, str] = {}
    for fid in list_factories():
        try:
            meta = _load_db(fid).get("meta") or {}
            out[fid] = str(meta.get("factory") or fid)
        except Exception:  # noqa: BLE001
            out[fid] = fid
    return out


def factory_aliases() -> Dict[str, str]:
    """{别名(小写): 板厂id}, 别名来自 YAML 的 meta.factory / meta.aliases"""
    out: Dict[str, str] = {}
    for fid in list_factories():
        out[fid.lower()] = fid
        try:
            meta = _load_db(fid).get("meta") or {}
        except Exception:  # noqa: BLE001
            continue
        names = [meta.get("factory")] + list(meta.get("aliases") or [])
        for nm in names:
            if nm:
                out[str(nm).strip().lower()] = fid
    return out


def factory_label(factory: str) -> str:
    return factory_labels().get(normalize_factory(factory), str(factory))


def normalize_factory(factory: str) -> str:
    """把用户输入 (id / 中文名 / 别名) 归一为能力库 id"""
    raw = str(factory or "").strip()
    key = raw.lower()
    files = _capability_files()
    if key in files:
        return key
    aliases = factory_aliases()
    if key in aliases:
        return aliases[key]
    if raw in aliases:
        return aliases[raw]
    if DEFAULT_FACTORY in files:
        return DEFAULT_FACTORY
    return sorted(files)[0] if files else DEFAULT_FACTORY


def load_capability_db(factory: str = DEFAULT_FACTORY) -> dict:
    """载入某板厂的完整能力库 (带 mtime 缓存, 改文件后自动重载)"""
    return _load_db(normalize_factory(factory))


def list_board_types(factory: str = DEFAULT_FACTORY) -> List[str]:
    return list(load_capability_db(factory).get("board_types", {}).keys())


def list_levels(board_type: str = "rigid", factory: str = DEFAULT_FACTORY) -> List[str]:
    db = load_capability_db(factory)
    bt = db.get("board_types", {}).get(board_type)
    if not bt:
        return []
    return list(bt.get("levels", []))


def level_label(board_type: str, level: str, factory: str = DEFAULT_FACTORY) -> str:
    db = load_capability_db(factory)
    bt = db.get("board_types", {}).get(board_type, {})
    return bt.get("level_labels", {}).get(level, level)


def normalize_level(level: str) -> str:
    return LEVEL_ALIASES.get(str(level).strip().lower(), "standard")


def load_capability(board_type: str = "rigid", level: str = "standard",
                    factory: str = DEFAULT_FACTORY) -> dict:
    db = load_capability_db(factory)
    board_type = board_type if board_type in db.get("board_types", {}) else "rigid"
    level = normalize_level(level)
    bt = db["board_types"][board_type]
    if level not in bt:
        level = bt["levels"][0]
    return dict(bt[level])


def _num(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mil_to_mm(value) -> Optional[float]:
    v = _num(value)
    return None if v is None else v * MIL_TO_MM


def _to_mm(spec) -> Optional[float]:
    """将带 {value, unit} 的量统一换算为 mm (支持 mm/um/mil)"""
    if not isinstance(spec, dict):
        return None
    v = _num(spec.get("value"))
    if v is None:
        return None
    unit = spec.get("unit", "mm")
    if unit == "um":
        return v / 1000.0
    if unit == "mil":
        return v * MIL_TO_MM
    return v


def get_line_spacing_capability(capability: dict, copper_um: float = 35.0) -> Tuple[Optional[float], Optional[float]]:
    """按成品铜厚(um)查询最小线宽/线距能力, 返回 (line_um, space_um)"""
    table = capability.get("trace", {}).get("min_line_spacing_by_copper", [])
    if not table:
        return None, None
    best = None
    for row in table:
        lo = row.get("copper_min_um", 0)
        hi = row.get("copper_max_um", 1e9)
        if lo <= copper_um <= hi:
            return _num(row.get("line_um")), _num(row.get("space_um"))
        if best is None and copper_um > hi:
            best = row
    if best is None:
        best = table[-1]
    return _num(best.get("line_um")), _num(best.get("space_um"))


def get_sm_bridge_capability(capability: dict, copper_oz: float = 1.0) -> Optional[float]:
    """按铜厚(oz)查询最小阻焊桥能力, 返回 mm。
    优先使用 min_bridge_single (单值能力, 与铜厚无关), 否则按 min_bridge_by_copper 分档。
    """
    sm = capability.get("solder_mask", {})
    single = sm.get("min_bridge_single")
    if single:
        return _to_mm(single)
    rows = sm.get("min_bridge_by_copper", [])
    if not rows:
        return None
    chosen = rows[0]
    for row in rows:
        if _num(row.get("copper_oz")) is not None and _num(row.get("copper_oz")) <= copper_oz:
            chosen = row
    v = _num(chosen.get("value"))
    unit = chosen.get("unit", "mil")
    if v is None:
        return None
    if unit == "um":
        return v / 1000.0
    if unit == "mil":
        return v * MIL_TO_MM
    return v


# ============================================================
# DFM 功能分析: 设计参数 -> 制程能力比对
# ============================================================
def analyze_design(design: dict, board_type: str = "rigid",
                   capability_level: str = "standard",
                   factory: str = DEFAULT_FACTORY, rules=None) -> dict:
    """
    design 支持的参数 (均可选, 长度类单位 mm, 铜厚 um):
      board_size: [x, y]            板尺寸 (mm)
      min_line_width                最小线宽 (mm)
      min_spacing                   最小线距 (mm)
      copper_um                     成品铜厚 (um), 默认 35
      copper_oz                     成品铜厚 (oz), 默认 1.0
      min_mech_drill / max_mech_drill  机械钻孔 (mm)
      min_laser_via                 激光孔 (mm)
      min_annular_ring              单边孔环 (mm)
      board_thickness               板厚 (mm)
      min_drill_for_ar              计算纵横比用最小孔径 (mm), 缺省取 min_mech_drill
      pth_to_pth_spacing            不同网络孔壁间距 (mm)
      copper_to_edge_inner / copper_to_edge_outer  线路到板边 (mm)
      npth_to_edge                  NPTH孔壁到板边 (mm)
      solder_mask_bridge            阻焊桥 (mm)
      solder_mask_opening           阻焊开窗 (mm)
      silkscreen_width              丝印线宽 (mm)
      silkscreen_clearance          丝印间隙 (mm)
      silkscreen_char_width/height  丝印字符 (mm)
      plug_hole_diameter / plug_board_thickness  塞孔孔径/板厚 (mm)
      coverlay_window / coverlay_spacing / coverlay_to_pad / coverlay_to_trace (mm, flex)
      test_pad_2wire / test_pad_4wire  电测PAD (mm)
    """
    factory = normalize_factory(factory)
    capability = load_capability(board_type, capability_level, factory=factory)
    level = normalize_level(capability_level)
    copper_um = _num(design.get("copper_um")) or 35.0
    copper_oz = _num(design.get("copper_oz")) or max(1.0, round(copper_um / 35.0, 1))

    items: List[dict] = []

    def add_check(category: str, name: str, design_val: Optional[float],
                  cap_val: Optional[float], unit: str = "mm",
                  higher_is_better: bool = False, note: str = ""):
        if design_val is None:
            return
        if cap_val is None:
            items.append({
                "category": category, "item": name, "design": design_val,
                "capability": None, "unit": unit, "status": "info",
                "message": f"{name}: 该等级无制程数据，需与板厂评估", "note": note,
            })
            return
        cap_val = round(cap_val, 4)
        design_val = round(design_val, 4)
        ok = design_val >= cap_val if higher_is_better else design_val <= cap_val
        if ok:
            status = "pass"
            message = f"{name}: 设计 {design_val}{unit} 满足能力 {cap_val}{unit}"
            near = (design_val >= cap_val * 0.8) if not higher_is_better else (design_val <= cap_val * 1.2)
            if near:
                status = "warn_limit"
                message = f"{name}: 设计 {design_val}{unit} 接近制程极限 {cap_val}{unit}"
            items.append({
                "category": category, "item": name, "design": design_val,
                "capability": cap_val, "unit": unit, "status": status,
                "message": message, "note": note,
            })
        else:
            direction = "≥" if higher_is_better else "≤"
            items.append({
                "category": category, "item": name, "design": design_val,
                "capability": cap_val, "unit": unit, "status": "fail",
                "message": f"{name}: 设计 {design_val}{unit} 超出制程能力 {direction}{cap_val}{unit}",
                "note": note,
            })

    trace = capability.get("trace", {})
    drill = capability.get("drill", {})
    sm = capability.get("solder_mask", {})
    silk = capability.get("silkscreen", {})
    panel = capability.get("panel", {})
    plug = capability.get("plug_hole", {})
    plating = capability.get("plating", {})
    test = capability.get("test", {})

    # ---- 拼板/板尺寸 ----
    bs = design.get("board_size")
    wp = panel.get("max_working_panel", {}).get("value")
    if bs and wp and len(bs) == 2 and all(_num(x) is not None for x in wp):
        bx, by = sorted(float(x) for x in bs)
        wx, wy = sorted(_num(x) for x in wp)
        ok = bx <= wx and by <= wy
        items.append({
            "category": "拼板", "item": "板尺寸",
            "design": f"{bs[0]}x{bs[1]}", "capability": f"{wx}x{wy}",
            "unit": "mm", "status": "pass" if ok else "fail",
            "message": f"板尺寸 {bs[0]}x{bs[1]}mm {'满足' if ok else '超出'}最大拼板 {wx}x{wy}mm",
            "note": panel.get("max_working_panel", {}).get("note", ""),
        })

    # ---- 板厚范围 ----
    board_thickness = _num(design.get("board_thickness"))
    bt_range = capability.get("lamination", {}).get("board_thickness", {})
    bt_min, bt_max = _num(bt_range.get("min_mm")), _num(bt_range.get("max_mm"))
    if board_thickness and bt_min is not None and bt_max is not None:
        ok = bt_min <= board_thickness <= bt_max
        items.append({
            "category": "压合", "item": "板厚范围",
            "design": board_thickness, "capability": f"{bt_min}-{bt_max}",
            "unit": "mm", "status": "pass" if ok else "fail",
            "message": f"板厚 {board_thickness}mm {'在' if ok else '不在'}能力范围 {bt_min}-{bt_max}mm",
            "note": bt_range.get("note", ""),
        })

    # ---- 线路 ----
    line_cap_um, space_cap_um = get_line_spacing_capability(capability, copper_um)
    add_check("线路", "最小线宽", _num(design.get("min_line_width")),
              None if line_cap_um is None else line_cap_um / 1000.0, "mm",
              higher_is_better=True, note=f"成品铜厚 {copper_um}um")
    add_check("线路", "最小线距", _num(design.get("min_spacing")),
              None if space_cap_um is None else space_cap_um / 1000.0, "mm",
              higher_is_better=True, note=f"成品铜厚 {copper_um}um")

    h2h = trace.get("hole_to_hole_spacing", {})
    add_check("线路", "不同网络孔壁间距", _num(design.get("pth_to_pth_spacing")),
              _num((h2h.get("pth_to_pth") or {}).get("value")), "mm",
              higher_is_better=True, note="涉及CAF问题")

    tbe = trace.get("trace_to_board_edge", {})
    add_check("线路", "内层线路到板边", _num(design.get("copper_to_edge_inner")),
              _num(tbe.get("inner")), "mm", higher_is_better=True)
    add_check("线路", "外层线路到板边", _num(design.get("copper_to_edge_outer")),
              _num(tbe.get("outer")), "mm", higher_is_better=True)

    # ---- 钻孔 ----
    add_check("钻孔", "最小机械钻孔", _num(design.get("min_mech_drill")),
              _num(drill.get("min_mech_drill", {}).get("value")), "mm",
              higher_is_better=True,
              note="软板" if board_type == "flex" else "硬板")
    add_check("钻孔", "最大机械钻孔", _num(design.get("max_mech_drill")),
              _num(drill.get("max_mech_drill", {}).get("value")), "mm")

    min_laser = _num(drill.get("min_laser_via", {}).get("value"))
    if min_laser is not None:
        add_check("钻孔", "最小激光孔", _num(design.get("min_laser_via")),
                  min_laser / 1000.0, "mm", higher_is_better=True)

    ann_mech_mm = _to_mm(drill.get("annular_ring_mech"))
    add_check("钻孔", "单边孔环(机械孔)", _num(design.get("min_annular_ring")),
              ann_mech_mm, "mm", higher_is_better=True,
              note=drill.get("annular_ring_mech", {}).get("note", ""))

    # ---- 纵横比 ----
    ar_drill = _num(design.get("min_drill_for_ar")) or _num(design.get("min_mech_drill"))
    pth_ar = _num(plating.get("pth_aspect_ratio", {}).get("value"))
    if board_thickness and ar_drill and pth_ar:
        if pth_ar < 2:
            items.append({
                "category": "钻孔", "item": "PTH纵横比",
                "design": round(board_thickness / ar_drill, 2), "capability": pth_ar,
                "unit": ":1", "status": "info",
                "message": f"PTH纵横比能力数据存疑({pth_ar:.0f}:1)，请与板厂确认",
                "note": plating.get("pth_aspect_ratio", {}).get("note", ""),
            })
        else:
            ar = board_thickness / ar_drill
            status = "pass" if ar <= pth_ar else "fail"
            if status == "pass" and ar >= pth_ar * 0.8:
                status = "warn_limit"
            if ar > pth_ar:
                ar_msg = f"纵横比 {ar:.2f}:1 (板厚{board_thickness}mm/孔径{ar_drill}mm) 超过能力 {pth_ar:.0f}:1"
            elif status == "warn_limit":
                ar_msg = f"纵横比 {ar:.2f}:1 (板厚{board_thickness}mm/孔径{ar_drill}mm) 接近制程极限 {pth_ar:.0f}:1"
            else:
                ar_msg = f"纵横比 {ar:.2f}:1 (板厚{board_thickness}mm/孔径{ar_drill}mm) 满足能力 {pth_ar:.0f}:1"
            items.append({
                "category": "钻孔", "item": "PTH纵横比",
                "design": round(ar, 2), "capability": pth_ar, "unit": ":1",
                "status": status,
                "message": ar_msg,
                "note": plating.get("pth_aspect_ratio", {}).get("note", ""),
            })

    add_check("钻孔", "NPTH孔到板边", _num(design.get("npth_to_edge")),
              _num(drill.get("npth_to_edge", {}).get("value")), "mm",
              higher_is_better=True, note="孔壁到板边，太小有破孔风险")

    # ---- 阻焊 ----
    bridge_cap = get_sm_bridge_capability(capability, copper_oz)
    add_check("阻焊", "最小阻焊桥", _num(design.get("solder_mask_bridge")),
              bridge_cap, "mm", higher_is_better=True, note=f"铜厚 {copper_oz}oz")
    open_cap_mm = _to_mm(sm.get("min_opening"))
    if open_cap_mm is not None:
        add_check("阻焊", "阻焊开窗最小尺寸", _num(design.get("solder_mask_opening")),
                  open_cap_mm, "mm", higher_is_better=True)

    # ---- 塞孔 ----
    plug_hole_d = _num(design.get("plug_hole_diameter"))
    plug_thk = _num(design.get("plug_board_thickness")) or board_thickness
    resin_min = plug.get("resin_plug_min", {})
    if plug_hole_d is not None and resin_min:
        cap_hole = _num(resin_min.get("hole_mm"))
        cap_thk = _num(resin_min.get("board_thickness_mm"))
        msgs = []
        if cap_hole is not None:
            msgs.append("孔径" + ("满足" if plug_hole_d >= cap_hole else f"低于最小{cap_hole}mm"))
        if cap_thk is not None and plug_thk is not None:
            msgs.append("板厚" + ("满足" if plug_thk >= cap_thk else f"低于最小{cap_thk}mm"))
        status = "pass" if all("满足" in m for m in msgs) else "fail"
        if msgs:
            items.append({
                "category": "塞孔", "item": "树脂塞孔条件",
                "design": plug_hole_d, "capability": cap_hole, "unit": "mm",
                "status": status,
                "message": f"树脂塞孔(孔径{plug_hole_d}mm/板厚{plug_thk}mm): {'，'.join(msgs)}",
                "note": resin_min.get("note", ""),
            })

    # ---- 丝印 ----
    add_check("丝印", "最小文字线宽", _num(design.get("silkscreen_width")),
              _to_mm(silk.get("min_line_width")), "mm",
              higher_is_better=True)
    add_check("丝印", "最小文字间隙", _num(design.get("silkscreen_clearance")),
              _to_mm(silk.get("min_clearance")), "mm",
              higher_is_better=True)
    char_cap = silk.get("min_char_size", {})
    cw = _mil_to_mm(char_cap.get("width_mil"))
    ch = _mil_to_mm(char_cap.get("height_mil"))
    dw, dh = _num(design.get("silkscreen_char_width")), _num(design.get("silkscreen_char_height"))
    if cw and ch and dw and dh:
        ok = dw >= cw and dh >= ch
        items.append({
            "category": "丝印", "item": "最小字符尺寸",
            "design": f"{dw}x{dh}", "capability": f"{round(cw,3)}x{round(ch,3)}",
            "unit": "mm", "status": "pass" if ok else "fail",
            "message": f"字符 {dw}x{dh}mm {'满足' if ok else '小于'}能力 {round(cw,3)}x{round(ch,3)}mm",
            "note": char_cap.get("note", ""),
        })

    # ---- 覆盖膜 (仅软板) ----
    if board_type == "flex":
        cvl = capability.get("coverlay", {})
        add_check("覆盖膜", "覆盖膜开窗最小间距", _num(design.get("coverlay_spacing")),
                  _num(cvl.get("min_window_spacing", {}).get("value")), "mm",
                  higher_is_better=True)
        add_check("覆盖膜", "开窗边缘到焊盘距离", _num(design.get("coverlay_to_pad")),
                  _num(cvl.get("window_to_pad", {}).get("value")), "mm",
                  higher_is_better=True)
        add_check("覆盖膜", "开窗边缘到线路距离", _num(design.get("coverlay_to_trace")),
                  _num(cvl.get("window_to_trace", {}).get("value")), "mm",
                  higher_is_better=True)
        win_cap = cvl.get("min_window_rect", {}).get("value")
        win_design = design.get("coverlay_window")
        if win_cap and win_design and len(win_cap) == 2 and isinstance(win_design, (list, tuple)):
            ok = win_design[0] >= win_cap[0] and win_design[1] >= win_cap[1]
            items.append({
                "category": "覆盖膜", "item": "覆盖膜最小开窗",
                "design": f"{win_design[0]}x{win_design[1]}",
                "capability": f"{win_cap[0]}x{win_cap[1]}", "unit": "mm",
                "status": "pass" if ok else "fail",
                "message": f"开窗 {win_design[0]}x{win_design[1]}mm "
                           f"{'满足' if ok else '小于'}最小开窗 {win_cap[0]}x{win_cap[1]}mm",
                "note": cvl.get("min_window_rect", {}).get("note", ""),
            })

    # ---- 测试 ----
    t2 = test.get("min_pad_2wire") or test.get("min_pad") or {}
    add_check("测试", "电测PAD(二线)", _num(design.get("test_pad_2wire")),
              _num(t2.get("value")), "mm", higher_is_better=True)
    t4 = test.get("min_pad_4wire", {})
    t4v = t4.get("value") if isinstance(t4, dict) else None
    if isinstance(t4v, (list, tuple)) and t4v:
        add_check("测试", "电测PAD(四线-宽)", _num(design.get("test_pad_4wire")),
                  _num(t4v[0]), "mm", higher_is_better=True)

    errors = [it for it in items if it["status"] == "fail"]
    warnings = [it for it in items if it["status"] == "warn_limit"]
    infos = [it for it in items if it["status"] == "info"]

    result = {
        "factory": factory,
        "factory_label": factory_label(factory),
        "board_type": board_type,
        "board_type_label": load_capability_db(factory)["board_types"][board_type].get("label", board_type),
        "capability_level": level,
        "capability_label": level_label(board_type, level, factory=factory),
        "copper_um": copper_um,
        "copper_oz": copper_oz,
        "items": items,
        "errors": [it["message"] for it in errors],
        "warnings": [it["message"] for it in warnings],
        "info": [it["message"] for it in infos],
        "error_count": len(errors),
        "warning_count": len(warnings),
        "passed": len(errors) == 0,
    }

    try:
        from dfm.core.issue import (build_fabrication_issues, build_info_stats,
                                    compute_risk_score, summarize_issues)
        issues = build_fabrication_issues(design, capability, items)
        scoring = rules.scoring() if rules is not None else None
        result["issues"] = issues
        result["issue_summary"] = summarize_issues(issues)
        result["risk"] = compute_risk_score(issues, scoring)
        result["info_stats"] = build_info_stats(design, capability, board_type)
    except Exception as exc:  # noqa: BLE001
        result["issues"] = []
        result["issue_summary"] = {}
        result["risk"] = {}
        result["info_stats"] = {}
        result["issue_error"] = str(exc)

    return result
