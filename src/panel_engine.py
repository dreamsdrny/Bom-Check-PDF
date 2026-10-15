"""
Evan_DFM: 排版(拼版)引擎
=============================================
术语:
    PCS   产品的单 PCS 尺寸 (单板)
    SET   连板 (PCS 连接后的连板尺寸图)
    Panel 工作稿 (SET 排到工作稿上)
    Sheet 大板/开料 (Panel 在大板上的裁切布局)

按《排版方式.docx》描述的能力实现 (Python 版, 桌面端可运行子集):
  - PCS -> SET(连板) -> Panel(工作稿) -> Sheet(大板/开料) 四级排版
  - 单料号 / 大小拼(AB) / 多料号混合 (2~4 种)
  - 排列方式: 矩形阵列(grid) / 花式拼-错位(staggered) / 横直料(hzdual)
  - 旋转角度: 支持任意自定义角度 (如 0/90/180/270 或 7°/8° 等), 自动搜索最优角度
  - 连接方式: V-Cut / 邮票孔 / 连接筋(连接) 及折断边
  - 工艺边 / WPnl 尺寸序列(凑表值) / 阻抗条 / 铜箔开料 / Mark / 定位孔 / 方向标记
  - PCS->SET 自动枚举 (行列组合取利用率最高)
  - 三图联动数据: 连板图 / 工作稿图 / 开料图 (A 蓝 / B 橙 分色)
"""
import math
from typing import Dict, List, Optional, Tuple

from src.dfm_rules import make_issue

PCB_LENGTH_UNIT = "mm"
INCH_TO_MM = 25.4
MM_TO_MIL = 1000.0 / 25.4      # 1 mm = 39.3700787 mil
MIL_TO_MM = 25.4 / 1000.0

# WPNL(工作稿) 标准尺寸选项 (inch); "0.4" 为材料厂商赠送, 不参与成本计算
WPNL_OPTIONS_INCH = [
    ("18.4x24.4", 18.4, 24.4),
    ("20.4x24.4", 20.4, 24.4),
    ("21.4x24.4", 21.4, 24.4),
    ("24.4x28.4", 24.4, 28.4),
    ("28.4x32.4", 28.4, 32.4),
]
DEFAULT_WPNL_INCH = (18.0, 24.0)          # 默认 18 x 24 inch
PANEL_FREE_INCH = 0.4                     # 赠送余量(不计成本)

# 排列方式类型 (PCB 拼板布局排版类型)
ARRANGEMENT_TYPES = [
    ("正排版", "forward", "同向顺拼 (大批量矩形板优先)"),
    ("旋转排版", "rotate", "旋转 180°/90° 拼板 (异形板省料)"),
    ("花式排版", "staggered", "异形错落拼板 (奇数行偏移)"),
    ("大小混排版", "mixed", "多尺寸配套拼板 (一套产品大小板一起)"),
    ("镜像阴阳排版", "mirror", "镜像阴阳拼板 (相邻单元镜像翻转)"),
]
ARRANGEMENT_ALIASES = {
    "正排版": "forward", "旋转排版": "rotate", "花式排版": "staggered",
    "大小混排版": "mixed", "镜像阴阳排版": "mirror",
    "grid": "forward", "forward": "forward", "rotate": "rotate",
    "staggered": "staggered", "hzdual": "hzdual", "mixed": "mixed", "mirror": "mirror",
}

# 多层板压合叠板定位工艺 (单独分类)
STACKUP_TYPES = [
    ("auto", "自动选择"),
    ("pin_lam", "Pin Lam (销钉定位叠板)"),
    ("mass_lam", "Mass Lam (无销钉热熔叠板)"),
]


def normalize_arrangement(value) -> str:
    return ARRANGEMENT_ALIASES.get(str(value or "").strip(), "forward")


def recommend_layout(p: dict) -> dict:
    """选型简易原则: 依据板形/批量/料号数/层数给出排版类型建议"""
    mode = str(p.get("mode", "single")).lower()
    irregular = bool(p.get("irregular_board"))
    qty = _num(p.get("board_qty"), None)
    kinds = 0
    if mode == "mixed":
        kinds = len(p.get("mixed_boards") or [])
    if mode == "ab" or kinds >= 2:
        code, why = "mixed", "一套产品含多种尺寸 → 大小混排版"
    elif irregular:
        code, why = "staggered", "不规则/异形外形 → 花式排版(或旋转排版省料)"
    elif p.get("arrangement") == "mirror":
        code, why = "mirror", "需阴阳/镜像对拼 → 镜像阴阳排版"
    elif qty is not None and qty >= 1000:
        code, why = "forward", "大批量矩形板 → 正排版(优先)"
    else:
        code, why = "rotate", "通用/省料 → 旋转排版(0/90/180°)"
    label = next((t[0] for t in ARRANGEMENT_TYPES if t[1] == code), code)
    return {"code": code, "label": label, "reason": why}


def recommend_stackup(p: dict) -> dict:
    """叠板定位工艺建议: HDI/高层数高精度 → Pin Lam; 普通4~8层 → Mass Lam"""
    layers = _num(p.get("layer_count"), None)
    hdi = bool(p.get("hdi"))
    if hdi or (layers is not None and layers >= 10):
        return {"code": "pin_lam", "label": "Pin Lam (销钉定位叠板)",
                "reason": "HDI/高层数高精度多层板 → Pin Lam"}
    if layers is not None and 4 <= layers <= 8:
        return {"code": "mass_lam", "label": "Mass Lam (无销钉热熔叠板)",
                "reason": "普通 4~8 层常规板 → Mass Lam"}
    return {"code": "auto", "label": "自动选择",
            "reason": "普通板默认 Mass Lam; 高精度/HDI 用 Pin Lam"}


def to_mil(mm_value: Optional[float]) -> Optional[float]:
    return None if mm_value is None else round(mm_value * MM_TO_MIL, 1)


def format_dim(mm_value: Optional[float], unit: str = "mm", nd: int = 2) -> str:
    """按当前显示单位格式化尺寸文本 (mm / mil)"""
    if mm_value is None:
        return ""
    if str(unit).lower() == "mil":
        return f"{mm_value * MM_TO_MIL:.0f}mil"
    return f"{mm_value:.{nd}f}mm"


def _decorate_views(views: dict, p: dict) -> None:
    """为三图添加尺寸标注 (连板/工作稿/大板 长×宽 + 工艺边宽) 与显示单位"""
    unit = p.get("units", "mm")
    rail = _num(p.get("rail_mm"), 4.0)
    labels = {"pcs": "PCS", "array": "ARRAY", "panel": "PANEL"}
    for key, view in views.items():
        cw, ch = view["container"]
        view["unit"] = unit
        feats = view.setdefault("features", [])
        feats.append({"type": "dim", "x1": 0, "y1": 0, "x2": cw, "y2": 0,
                      "value": cw, "orient": "h", "label": labels.get(key, "")})
        feats.append({"type": "dim", "x1": 0, "y1": 0, "x2": 0, "y2": ch,
                      "value": ch, "orient": "v", "label": labels.get(key, "")})
        board = view.get("board")
        if board and len(board) == 2:
            bx = _num(view.get("rail_mm"), 0.0) or 0.0
            feats.append({"type": "dim", "x1": bx, "y1": bx, "x2": bx + board[0], "y2": bx,
                          "value": board[0], "orient": "h", "label": "单板宽"})
            feats.append({"type": "dim", "x1": bx, "y1": bx, "x2": bx, "y2": bx + board[1],
                          "value": board[1], "orient": "v", "label": "单板高"})
        rail = _num(view.get("rail_mm"), _num(p.get("rail_mm"), 4.0)) or 0.0
        if rail > 0:
            feats.append({"type": "dim", "x1": 0, "y1": ch, "x2": rail, "y2": ch,
                          "value": rail, "orient": "h", "label": "工艺边宽"})
            feats.append({"type": "dim", "x1": 0, "y1": ch - rail, "x2": 0, "y2": ch,
                          "value": rail, "orient": "v", "label": "工艺边宽"})

DEFAULT_PARAMS = {
    "mode": "single",              # single | ab | mixed
    "arrangement": "forward",      # forward|rotate|staggered|mixed|mirror|hzdual
    "panel_style": "array",        # array | single_pcs (单PCS+工艺边)
    "stackup_method": "auto",      # auto | pin_lam | mass_lam
    "layer_count": None, "hdi": False, "irregular_board": False, "board_qty": None,
    "units": "mm",                 # 输入单位: mm | mil
    "board_w": 31.0, "board_h": 31.2,
    "board_outline": None,         # DXF 导入的外形轮廓 (归一化 mm); 提供则覆盖 board_w/h
    "board_a_w": 31.0, "board_a_h": 31.2,
    "board_b_w": 40.0, "board_b_h": 25.0,
    "mixed_boards": [],            # [{label,w,h}]
    "pcs_gap": 0.0,
    "rotation_candidates": None,   # PCS 内部旋转角度列表; None/NA = 不旋转(默认)
    "set_rows": None, "set_cols": None,
    "set_gap": 2.0,
    "wp_nl_option": "",            # WPNL 标准选项 (如 "18.4x24.4"), 优先于 wpnl_w/h
    "panel_free_inch": PANEL_FREE_INCH,
    "wpnl_w": 457.2, "wpnl_h": 609.6,   # 默认 18x24 inch
    "wpnl_sizes": [],              # 凑表值 [[w,h], ...]
    "wpnl_gap": 4.0,
    "sheet_w": 660.4, "sheet_h": 830.0,
    "rail_mm": 4.0,                # PANEL(工作稿) 工艺边宽度 (支持自定义 2/5/10mm 等)
    "array_rail_mm": None,         # ARRAY(连板) 工艺边宽度 (None → 同 rail_mm)
    "pcs_rail_mm": None,           # PCS 级工艺边宽度 (None → 同 rail_mm)
    "pcs_to_edge": 0.0,            # PCS 到工艺边间距
    "edge_rib_mm": 2.0,            # PCS 与工艺边之间的连接筋宽度(默认 2.0mm)
    "rail_rib_count": 3,           # 每边连接筋数量
    "rib_sides": "all",            # 连接筋位置: all(四边) | long(长边) | short(短边)
    "separation": "",              # 分板方式: vcut | stamp | routed | break (空=按开关)
    "stamp_diameter_mm": 0.5,      # 邮票孔孔径
    "alternate": {"enabled": False, "mode": "mirror", "by": "row"},   # 交替拼
    "mid_cut": {"enabled": False, "direction": "v", "width": 2.0, "count": 1},  # 中裁
    "allow_rotate": False,         # 是否允许 PCS 旋转 (默认 NA)
    "vcut": True, "stamp_holes": False, "stamp_pitch": 2.0,
    "ribs": {"enabled": False, "width": 1.0, "count": 1},
    "break_off": True, "break_off_mm": 3.0,
    "impedance": {"enabled": False, "count": 2, "length_mm": 20.0,
                  "width_mm": 3.0, "gap_mm": 5.0},
    "copper_foil": {"enabled": False},
    "mark_count": 3, "mark_to_edge": 3.5,
    "tooling_count": 2, "tooling_d": 3.0,
    "min_utilization": 0.7,
}

COLOR_A = "#2f7ed8"
COLOR_B = "#f29100"
MIX_PALETTE = ["#2f7ed8", "#f29100", "#8e44ad", "#16a085"]


def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def rotate_bbox(w: float, h: float, angle_deg: float) -> Tuple[float, float]:
    """矩形旋转任意角度后的轴对齐包围盒尺寸"""
    a = math.radians(angle_deg % 360)
    c, s = abs(math.cos(a)), abs(math.sin(a))
    return w * c + h * s, w * s + h * c


def rotated_corners(cx: float, cy: float, ow: float, oh: float, angle_deg: float):
    """以 (cx,cy) 为中心, 将 ow x oh 矩形旋转 angle 后的四角坐标(逆时针度)"""
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    pts = [(-ow / 2, -oh / 2), (ow / 2, -oh / 2), (ow / 2, oh / 2), (-ow / 2, oh / 2)]
    return [(cx + x * c - y * s, cy + x * s + y * c) for x, y in pts]


def _orientations(w, h, angles):
    out = []
    seen = set()
    for ang in angles:
        try:
            ang = float(ang) % 360
        except (TypeError, ValueError):
            continue
        key = round(ang, 3)
        if key in seen:
            continue
        seen.add(key)
        bw, bh = rotate_bbox(w, h, ang)
        out.append((round(bw, 4), round(bh, 4), key))
    return out or [(w, h, 0.0)]


def _transform_outline(outline, x, y, bw, bh, ang):
    """将归一化外形 (0..bw,0..bh) 旋转 ang 后放入单元左下角 (x,y)"""
    pts = list(outline)
    if ang % 360:
        a = math.radians(ang)
        c, s = math.cos(a), math.sin(a)
        cx, cy = bw / 2.0, bh / 2.0
        pts = [(cx + (px - cx) * c - (py - cy) * s, cy + (px - cx) * s + (py - cy) * c)
               for px, py in outline]
    minx = min(p[0] for p in pts)
    miny = min(p[1] for p in pts)
    return [(round(x + px - minx, 3), round(y + py - miny, 3)) for px, py in pts]


def _grid(avail_w, avail_h, cw, ch, gap):
    if cw <= 0 or ch <= 0:
        return 0, 0
    nx = int(math.floor((avail_w + gap) / (cw + gap)))
    ny = int(math.floor((avail_h + gap) / (ch + gap)))
    return max(0, nx), max(0, ny)


def _best_orientation(avail_w, avail_h, w, h, gap, allow_rotate, angles=None):
    angles = angles if angles is not None else ([0, 90] if allow_rotate else [0])
    best = None
    for bw, bh, ang in _orientations(w, h, angles):
        nx, ny = _grid(avail_w, avail_h, bw, bh, gap)
        count = nx * ny
        if best is None or count > best["count"]:
            best = {"nx": nx, "ny": ny, "count": count, "cw": bw, "ch": bh,
                    "angle": ang, "rotated": abs(ang % 180) > 1e-6}
    return best


# ============================================================
# 四级排版
# ============================================================
def _make_view(title, container, items, features=None, unit="mm"):
    return {"title": title, "container": [round(container[0], 3), round(container[1], 3)],
            "items": items, "features": features or [], "unit": unit}


def _choose_wpnl(p):
    opt = str(p.get("wp_nl_option") or "").strip()
    if opt:
        for label, wi, hi in WPNL_OPTIONS_INCH:
            if label == opt:
                return wi * INCH_TO_MM, hi * INCH_TO_MM
    sizes = p.get("wpnl_sizes") or []
    if sizes:
        return float(sizes[0][0]), float(sizes[0][1])
    return _num(p.get("wpnl_w"), 457.2), _num(p.get("wpnl_h"), 609.6)


def _design_single(p) -> dict:
    bw, bh = _num(p["board_w"]), _num(p["board_h"])
    outline = p.get("board_outline")
    if outline:
        xs = [pt[0] for pt in outline]
        ys = [pt[1] for pt in outline]
        bw = max(xs) - min(xs)
        bh = max(ys) - min(ys)
        outline = [(pt[0] - min(xs), pt[1] - min(ys)) for pt in outline]
    pcs_gap = _num(p.get("pcs_gap"), 0.0)
    set_gap = _num(p.get("set_gap"), 2.0)
    rail = _num(p.get("rail_mm"), 4.0)
    break_off = _num(p.get("break_off_mm"), 3.0) if p.get("break_off") else 0.0
    wpnl_w, wpnl_h = _choose_wpnl(p)
    sheet_w, sheet_h = _num(p.get("sheet_w"), 660.4), _num(p.get("sheet_h"), 830.0)
    wpnl_gap = _num(p.get("wpnl_gap"), 4.0)
    edge = rail + _num(p.get("pcs_to_edge"), 0.0)   # PANEL 工艺边 + PCS到工艺边间距
    pcs_rail = _num(p.get("pcs_rail_mm"), None)
    pcs_rail = rail if pcs_rail is None else pcs_rail
    array_rail = _num(p.get("array_rail_mm"), None)
    array_rail = rail if array_rail is None else array_rail

    uw, uh = wpnl_w - 2 * edge, wpnl_h - 2 * edge

    rows_fixed = p.get("set_rows")
    cols_fixed = p.get("set_cols")
    single_pcs = str(p.get("panel_style", "array")) == "single_pcs"
    if single_pcs:
        rows_fixed, cols_fixed = 1, 1            # 单PCS + 工艺边: 每工作稿仅 1 PCS
    arrangement = normalize_arrangement(p.get("arrangement"))
    cand = p.get("rotation_candidates")
    if cand:
        angles = [float(a) % 360 for a in cand]     # PCS 自定义旋转 (0-360°)
    elif arrangement == "rotate":
        angles = [0, 90, 180]                       # 旋转排版: 默认 0/90/180
    elif p.get("allow_rotate"):
        angles = [0, 90]                            # 勾选允许旋转
    else:
        angles = [0]                                # NA: 不旋转
    best = None
    for obw, obh, ang in _orientations(bw, bh, angles):
        if rows_fixed and cols_fixed:
            combos = [(int(rows_fixed), int(cols_fixed))]
        else:
            max_r = max(1, int(uh // (obh + pcs_gap)) or 1)
            max_c = max(1, int(uw // (obw + pcs_gap)) or 1)
            combos = [(r, c) for r in range(1, max_r + 1) for c in range(1, max_c + 1)]
        for r, c in combos:
            set_w = c * obw + (c - 1) * pcs_gap + 2 * break_off
            set_h = r * obh + (r - 1) * pcs_gap + 2 * break_off
            arr_w = set_w + 2 * array_rail          # ARRAY + 工艺边
            arr_h = set_h + 2 * array_rail
            if arr_w > uw or arr_h > uh:
                continue
            s_nx, s_ny = _grid(uw, uh, arr_w, arr_h, set_gap)
            if single_pcs:
                s_nx, s_ny = 1, 1
            sets = s_nx * s_ny
            pcs_per_wpnl = sets * r * c
            area = pcs_per_wpnl * bw * bh
            if best is None or area > best["area"]:
                best = {"rows": r, "cols": c, "set_w": set_w, "set_h": set_h,
                        "arr_w": arr_w, "arr_h": arr_h,
                        "set_nx": s_nx, "set_ny": s_ny, "sets": sets,
                        "pcs_per_wpnl": pcs_per_wpnl, "area": area, "break_off": break_off,
                        "angle": ang, "bw": obw, "bh": obh}

    if not best:
        return {"ok": False, "error": "单PCS 无法排入工作稿",
                "issues": [make_issue(code="PNL-01", category="拼板", rule="板尺寸", severity="危险",
                                      description=f"单PCS {bw}x{bh}mm 无法放入工作稿 {wpnl_w}x{wpnl_h}mm")]}

    items_set, items_wpnl = [], []
    set_w, set_h = best["set_w"], best["set_h"]
    arr_w, arr_h = best["arr_w"], best["arr_h"]
    obw, obh, ang = best["bw"], best["bh"], best["angle"]
    col_pitch = arr_w + set_gap
    row_pitch = arr_h + set_gap
    mirror = arrangement == "mirror"
    alt = p.get("alternate") or {}
    alt_on = bool(alt.get("enabled"))
    alt_mode = str(alt.get("mode", "mirror")).lower()
    alt_by = str(alt.get("by", "row")).lower()

    # ARRAY 图: PCS → ARRAY(连板), 编号 P1..PN, 含 ARRAY 工艺边
    for r in range(best["rows"]):
        for c in range(best["cols"]):
            idx = r * best["cols"] + c
            xpos = round(array_rail + break_off + c * (obw + pcs_gap), 3)
            ypos = round(array_rail + break_off + r * (obh + pcs_gap), 3)
            item = {
                "x": xpos, "y": ypos,
                "w": obw, "h": obh, "angle": ang, "orig_w": bw, "orig_h": bh,
                "mirror": mirror and ((r + c) % 2 == 1),
                "label": f"P{idx + 1}", "color": "#000000", "frame": True,
            }
            if outline:
                item["poly"] = _transform_outline(outline, xpos, ypos, bw, bh, ang)
            items_set.append(item)

    # PANEL 图: ARRAY → PANEL(工作稿), 支持错位 / 镜像阴阳 / 交替拼
    for iy in range(best["set_ny"]):
        for ix in range(best["set_nx"]):
            offset = 0.0
            if arrangement == "staggered" and iy % 2 == 1:
                offset = col_pitch / 2.0
                if offset + arr_w > uw:
                    continue
            it = {
                "x": round(edge + ix * col_pitch + offset, 3),
                "y": round(edge + iy * row_pitch, 3),
                "w": arr_w, "h": arr_h,
                "label": f"A{iy * best['set_nx'] + ix + 1}", "color": COLOR_A, "kind": "array",
            }
            if alt_on and ((iy % 2 == 1) if alt_by == "row" else (ix % 2 == 1)):
                if alt_mode == "rotate":
                    it["angle"] = 90
                else:
                    it["mirror"] = True
            if mirror and (iy + ix) % 2 == 1:
                it["mirror"] = True
            items_wpnl.append(it)

    # 开料图 (Sheet): Panel -> 大板 (工作稿不旋转)
    items_sheet = _layout_sheet(sheet_w, sheet_h, wpnl_w, wpnl_h, wpnl_gap, rail)

    # ---- PCS 图: 单PCS + 连接筋 + 工艺边 ----
    pcs_w, pcs_h = bw + 2 * pcs_rail, bh + 2 * pcs_rail
    pcs_item = {"x": round(pcs_rail, 3), "y": round(pcs_rail, 3),
                "w": bw, "h": bh, "label": "P1", "color": "#000000", "frame": True}
    if outline:
        pcs_item["poly"] = _transform_outline(outline, pcs_rail, pcs_rail, bw, bh, 0)
    features_pcs = _rail_frame_features(pcs_w, pcs_h, pcs_rail, pcs_rail, p)
    features_pcs += _process_features([pcs_item], pcs_w, pcs_h, p, "set")

    # ---- ARRAY 图: PCS 阵列 + 工艺边 ----
    features_array = _process_features(items_set, arr_w, arr_h, p, "set")
    features_array += _rail_frame_features(arr_w, arr_h, array_rail, array_rail, p)

    # ---- PANEL 图: ARRAY 排布 + 工艺边 + 连接筋 + 阻抗条 + 中裁 ----
    features_panel = _process_features(items_wpnl, wpnl_w, wpnl_h, p, "panel")
    features_panel += _rail_frame_features(wpnl_w, wpnl_h, rail, edge, p)
    features_panel += _impedance_between_arrays(items_wpnl, p)
    features_panel += _mid_cut_features(wpnl_w, wpnl_h, edge, p)

    wpnl_per_sheet = len(items_sheet)
    pcs_per_wpnl = best["pcs_per_wpnl"]
    pcs_per_sheet = pcs_per_wpnl * wpnl_per_sheet
    util_set = (best["rows"] * best["cols"] * bw * bh) / max(set_w * set_h, 1e-9)
    util_wpnl = (len(items_wpnl) * arr_w * arr_h) / max(wpnl_w * wpnl_h, 1e-9)
    util_sheet = (wpnl_per_sheet * wpnl_w * wpnl_h) / max(sheet_w * sheet_h, 1e-9)
    overall = (pcs_per_sheet * bw * bh) / max(sheet_w * sheet_h, 1e-9)

    views = {
        "pcs": _make_view("PCS 图 (单PCS + 连接筋 + 工艺边)", (pcs_w, pcs_h), [pcs_item], features_pcs),
        "array": _make_view("ARRAY 图 (PCS 阵列 + 工艺边)", (arr_w, arr_h), items_set, features_array),
        "panel": _make_view("PANEL 图 (工作稿下的 ARRAY 排布)", (wpnl_w, wpnl_h), items_wpnl, features_panel),
    }
    views["pcs"]["rail_mm"] = pcs_rail
    views["pcs"]["board"] = [bw, bh]          # 单电路板净尺寸 (不含工艺边)
    views["array"]["rail_mm"] = array_rail
    views["panel"]["rail_mm"] = rail
    _decorate_views(views, p)

    plan = _assemble_plan(p, "single", {
        "pcs_per_array": best["rows"] * best["cols"],
        "arrays_per_panel": len(items_wpnl),
        "panel_per_sheet": wpnl_per_sheet,
        "pcs_per_panel": pcs_per_wpnl,
        "pcs_per_sheet": pcs_per_sheet,
    }, {"set": util_set, "panel": util_wpnl, "wpnl": util_wpnl, "sheet": util_sheet, "overall": overall},
        views, {"rows": best["rows"], "cols": best["cols"],
                "array_w": arr_w, "array_h": arr_h, "set_w": set_w, "set_h": set_h,
                "angle": ang,
                "rails": {"pcs": pcs_rail, "array": array_rail, "panel": rail}},
        wpnl_w, wpnl_h, sheet_w, sheet_h)
    return plan


def _layout_sheet(sheet_w, sheet_h, wpnl_w, wpnl_h, gap, rail):
    # 工作稿(WPNL) 不旋转: 一律正排
    uw, uh = sheet_w - 2 * rail, sheet_h - 2 * rail
    items = []
    nx, ny = _grid(uw, uh, wpnl_w, wpnl_h, gap)
    for iy in range(ny):
        for ix in range(nx):
            items.append({"x": round(rail + ix * (wpnl_w + gap), 3),
                          "y": round(rail + iy * (wpnl_h + gap), 3),
                          "w": wpnl_w, "h": wpnl_h,
                          "label": f"W{len(items)+1}", "color": COLOR_A})
    return items


def _process_features(items, cw, ch, p, level):
    feats = []
    if not items:
        return feats
    sep = str(p.get("separation", "") or "").lower()      # 分板方式
    do_vcut = (sep == "vcut") if sep else bool(p.get("vcut"))
    do_routed = (sep == "routed")
    do_stamp = (sep == "stamp") if sep else bool(p.get("stamp_holes"))
    stamp_d = _num(p.get("stamp_diameter_mm"), 0.5) or 0.5
    if do_vcut or do_routed:
        cols = sorted({round(it["x"], 3) for it in items})
        for x in cols[1:]:
            feats.append({"type": "vcut", "x1": x, "y1": 0, "x2": x, "y2": ch,
                          "solid": do_routed})
        rows = sorted({round(it["y"], 3) for it in items})
        for y in rows[1:]:
            feats.append({"type": "vcut", "x1": 0, "y1": y, "x2": cw, "y2": y,
                          "solid": do_routed})
    if do_stamp:
        pitch = _num(p.get("stamp_pitch"), 2.0) or 2.0
        if max(ch, cw) / max(pitch, 1e-6) > 200:      # 防止邮票孔数量爆炸
            pitch = max(ch, cw) / 200.0
        for x in sorted({round(it["x"], 3) for it in items})[1:]:
            y = 0.0
            while y <= ch:
                feats.append({"type": "stamp", "x": x, "y": round(y, 3), "d": stamp_d})
                y += pitch
        for y in sorted({round(it["y"], 3) for it in items})[1:]:
            x = 0.0
            while x <= cw:
                feats.append({"type": "stamp", "x": round(x, 3), "y": y, "d": stamp_d})
                x += pitch
    ribs = p.get("ribs") or {}
    if ribs.get("enabled") and items:
        rw = _num(ribs.get("width"), 1.0) or 1.0
        rc = max(1, int(_num(ribs.get("count"), 1) or 1))
        by_row, by_col = {}, {}
        for it in items:
            by_row.setdefault(round(it["y"], 3), []).append(it)
            by_col.setdefault(round(it["x"], 3), []).append(it)
        for _y, row in by_row.items():
            row.sort(key=lambda a: a["x"])
            for a, b in zip(row, row[1:]):
                gp = b["x"] - (a["x"] + a["w"])
                if gp > 1e-3:
                    for k in range(rc):
                        fy = a["y"] + (k + 1) / (rc + 1) * a["h"] - rw / 2
                        feats.append({"type": "rib", "x": round(a["x"] + a["w"], 3),
                                      "y": round(fy, 3), "w": round(gp, 3), "h": rw})
        for _x, col in by_col.items():
            col.sort(key=lambda a: a["y"])
            for a, b in zip(col, col[1:]):
                gp = b["y"] - (a["y"] + a["h"])
                if gp > 1e-3:
                    for k in range(rc):
                        fx = a["x"] + (k + 1) / (rc + 1) * a["w"] - rw / 2
                        feats.append({"type": "rib", "x": round(fx, 3),
                                      "y": round(a["y"] + a["h"], 3), "w": rw, "h": round(gp, 3)})
    if p.get("break_off") and level == "set":
        feats.append({"type": "break_off", "x": 0, "y": 0, "w": cw, "h": ch})
    return feats


def _impedance_between_arrays(items, p):
    """阻抗条: 放置在 ARRAY 之间的间距处; 数量/长/宽/间距 可自定义 (mm/mil)"""
    imp = p.get("impedance") or {}
    if not imp.get("enabled") or not items:
        return []
    count = max(1, int(_num(imp.get("count"), 2) or 2))
    length = _num(imp.get("length_mm"), 20.0) or 20.0
    width = _num(imp.get("width_mm"), 3.0) or 3.0
    gap = _num(imp.get("gap_mm"), 5.0) or 5.0
    rows = sorted({round(it["y"], 3) for it in items})
    heights = sorted({round(it["h"], 3) for it in items})
    cell_h = heights[-1] if heights else 0.0
    xs = [it["x"] for it in items]
    xe = [it["x"] + it["w"] for it in items]
    cx = (min(xs) + max(xe)) / 2.0 if xs else 0.0
    total = count * length + (count - 1) * gap
    start = cx - total / 2.0
    feats = []
    for i in range(len(rows) - 1):
        y0 = rows[i] + cell_h
        y1 = rows[i + 1]
        gapy = y1 - y0
        if gapy <= 0.05:
            continue
        ybar = y0 + max((gapy - width) / 2.0, 0.0)
        for k in range(count):
            feats.append({"type": "impedance", "x": round(start + k * (length + gap) + length / 2, 3),
                          "y": round(ybar, 3), "w": length, "h": width})
    return feats


def _rail_frame_features(pw, ph, rail, edge, p):
    """工艺边边框 + 内容区与工艺边之间的连接筋 (默认 2.0mm)。
    三级视图通用: PCS+连接筋+工艺边 / ARRAY+工艺边 / PANEL+工艺边。
    rib_sides: all(四边) | long(长边) | short(短边)
    """
    rail = max(_num(rail, 4.0) or 0.0, 0.0)
    rib = _num(p.get("edge_rib_mm"), 2.0) or 2.0
    count = max(1, int(_num(p.get("rail_rib_count"), 3) or 3))
    sides = str(p.get("rib_sides", "all") or "all").lower()
    if rail <= 0:
        return []
    feats = [
        {"type": "rail", "x": 0, "y": 0, "w": pw, "h": rail},
        {"type": "rail", "x": 0, "y": ph - rail, "w": pw, "h": rail},
        {"type": "rail", "x": 0, "y": 0, "w": rail, "h": ph},
        {"type": "rail", "x": pw - rail, "y": 0, "w": rail, "h": ph},
    ]
    long_is_tb = pw >= ph          # 长边 = 上下(水平)边
    do_tb = sides == "all" or (sides == "long" and long_is_tb) or (sides == "short" and not long_is_tb)
    do_lr = sides == "all" or (sides == "long" and not long_is_tb) or (sides == "short" and long_is_tb)
    gap = max(edge - rail, rib)
    if do_tb:
        for i in range(count):
            fx = edge + (i + 0.5) / count * max(pw - 2 * edge, rib)
            feats.append({"type": "rib", "x": round(fx - rib / 2, 3), "y": round(rail, 3),
                          "w": rib, "h": round(gap, 3)})
            feats.append({"type": "rib", "x": round(fx - rib / 2, 3), "y": round(ph - rail - gap, 3),
                          "w": rib, "h": round(gap, 3)})
    if do_lr:
        for i in range(count):
            fy = edge + (i + 0.5) / count * max(ph - 2 * edge, rib)
            feats.append({"type": "rib", "x": round(rail, 3), "y": round(fy - rib / 2, 3),
                          "w": round(gap, 3), "h": rib})
            feats.append({"type": "rib", "x": round(pw - rail - gap, 3), "y": round(fy - rib / 2, 3),
                          "w": round(gap, 3), "h": rib})
    return feats


def _mid_cut_features(pw, ph, edge, p):
    """中裁: 在 Panel 内部按行/列加裁切槽 (宽度/数量可设)"""
    mc = p.get("mid_cut") or {}
    if not mc.get("enabled"):
        return []
    width = _num(mc.get("width"), 2.0) or 2.0
    count = max(1, int(_num(mc.get("count"), 1) or 1))
    direction = str(mc.get("direction", "v")).lower()
    feats = []
    if direction == "v":
        span = max(pw - 2 * edge, 0.0)
        for i in range(count):
            x = edge + (i + 1) * span / (count + 1)
            feats.append({"type": "midcut", "x1": round(x - width / 2, 3), "y1": round(edge, 3),
                          "x2": round(x + width / 2, 3), "y2": round(ph - edge, 3), "w": width})
    else:
        span = max(ph - 2 * edge, 0.0)
        for i in range(count):
            y = edge + (i + 1) * span / (count + 1)
            feats.append({"type": "midcut", "x1": round(edge, 3), "y1": round(y - width / 2, 3),
                          "x2": round(pw - edge, 3), "y2": round(y + width / 2, 3), "w": width})
    return feats


def _design_multi(p, mode) -> dict:
    """AB 大小拼 / 多料号混合: 在工作板上货架排料, 再拼到大板"""
    rail = _num(p.get("rail_mm"), 4.0)
    break_off = 0.0
    wpnl_w, wpnl_h = _choose_wpnl(p)
    sheet_w, sheet_h = _num(p.get("sheet_w"), 660.4), _num(p.get("sheet_h"), 830.0)
    wpnl_gap = _num(p.get("wpnl_gap"), 4.0)
    gap = _num(p.get("set_gap"), 2.0)
    rotate = bool(p.get("allow_rotate", False))

    if mode == "ab":
        kinds = [
            {"label": "A", "w": _num(p["board_a_w"]), "h": _num(p["board_a_h"]), "color": COLOR_A, "limit": None},
            {"label": "B", "w": _num(p["board_b_w"]), "h": _num(p["board_b_h"]), "color": COLOR_B, "limit": None},
        ]
    else:
        kinds = []
        for i, b in enumerate(p.get("mixed_boards") or []):
            kinds.append({"label": b.get("label", chr(65 + i)), "w": _num(b.get("w")),
                          "h": _num(b.get("h")), "color": MIX_PALETTE[i % len(MIX_PALETTE)],
                          "limit": b.get("limit")})
        if not kinds:
            kinds = [{"label": "A", "w": _num(p["board_w"]), "h": _num(p["board_h"]),
                      "color": COLOR_A, "limit": None}]

    edge = rail + _num(p.get("pcs_to_edge"), 0.0)
    uw, uh = wpnl_w - 2 * edge, wpnl_h - 2 * edge
    arrangement = normalize_arrangement(p.get("arrangement"))
    cand = p.get("rotation_candidates")
    if cand:
        angles = [float(a) % 360 for a in cand]     # PCS 自定义旋转 (0-360°)
    elif arrangement == "rotate":
        angles = [0, 90, 180]                       # 旋转排版: 默认 0/90/180
    elif p.get("allow_rotate"):
        angles = [0, 90]                            # 勾选允许旋转
    else:
        angles = [0]                                # NA: 不旋转
    placements, y_cursor, counts = _pack_columns(uw, uh, edge, kinds, gap, rotate, angles)
    if not placements:
        return {"ok": False, "error": "无可行排料",
                "issues": [make_issue(code="PNL-01", category="拼板", rule="板料", severity="危险",
                                      description="在工作稿上无法排入任何板料")]}

    mirror = arrangement == "mirror"
    items_wpnl = [{"x": round(pl["x"], 3), "y": round(pl["y"], 3), "w": pl["w"], "h": pl["h"],
                   "label": pl["label"], "color": pl["color"], "rotated": pl.get("rotated", False),
                   "angle": pl.get("angle", 0.0), "orig_w": pl.get("orig_w"), "orig_h": pl.get("orig_h"),
                   "mirror": mirror and (i % 2 == 1)}
                  for i, pl in enumerate(placements)]
    items_sheet = _layout_sheet(sheet_w, sheet_h, wpnl_w, wpnl_h, wpnl_gap, rail)
    unit_items, ux, umaxh = [], 3.0, 0.0
    for k in kinds:
        unit_items.append({"x": round(ux, 3), "y": 3.0, "w": k["w"], "h": k["h"],
                           "label": k["label"], "color": k["color"], "angle": 0})
        ux += k["w"] + 6.0
        umaxh = max(umaxh, k["h"])
    feats_panel = _process_features(items_wpnl, wpnl_w, wpnl_h, p, "panel")
    feats_panel += _rail_frame_features(wpnl_w, wpnl_h, rail, edge, p)
    feats_panel += _impedance_between_arrays(items_wpnl, p)
    feats_panel += _mid_cut_features(wpnl_w, wpnl_h, edge, p)
    views = {
        "pcs": _make_view("PCS 图 (各料号单板定义, 蓝=A 橙=B)", (ux, umaxh + 6.0), unit_items, []),
        "array": _make_view("ARRAY 图 (各料号 ARRAY 定义)", (ux, umaxh + 6.0), unit_items, []),
        "panel": _make_view("PANEL 图 (ARRAY 在 PANEL 上的排料 + 工艺边 + 连接筋)",
                            (wpnl_w, wpnl_h), items_wpnl, feats_panel),
    }
    _decorate_views(views, p)
    board_area = sum(pl["w"] * pl["h"] for pl in placements)
    util_wpnl = board_area / max(wpnl_w * wpnl_h, 1e-9)
    wpnl_per_sheet = len(items_sheet)
    util_sheet = (wpnl_per_sheet * wpnl_w * wpnl_h) / max(sheet_w * sheet_h, 1e-9)
    total_per_sheet = sum(counts.values()) * wpnl_per_sheet
    overall = (sum(c * kinds[i]["w"] * kinds[i]["h"] for i, c in enumerate(counts.values()))
               * wpnl_per_sheet) / max(sheet_w * sheet_h, 1e-9)
    plan = _assemble_plan(p, mode, {
        "counts_per_wpnl": counts, "wp_nl_per_sheet": wpnl_per_sheet,
        "boards_per_sheet": total_per_sheet,
    }, {"set": util_wpnl, "wpnl": util_wpnl, "sheet": util_sheet, "overall": overall},
        views, {"kinds": kinds}, wpnl_w, wpnl_h, sheet_w, sheet_h)
    return plan


def _pack_columns(avail_w, avail_h, rail, kinds, gap, allow_rotate, angles=None):
    """列带分配: 为每种料号分配若干列, 每列满高堆放; 贪心最大化面积。
    支持 AB 大小拼与多料号混合, 支持任意自定义旋转角度。"""
    n = len(kinds)
    w = [k["w"] for k in kinds]
    h = [k["h"] for k in kinds]
    angles = angles if angles is not None else ([0, 90] if allow_rotate else [0])
    rotated = [False] * n
    angs = [0.0] * n
    for i in range(n):
        best = None
        for cw, ch, ang in _orientations(w[i], h[i], angles):
            nx, ny = _grid(avail_w, avail_h, cw, ch, gap)
            if best is None or nx * ny > best[0]:
                best = (nx * ny, cw, ch, ang)
        if best:
            w[i], h[i], angs[i] = best[1], best[2], best[3]
            rotated[i] = abs(best[3] % 180) > 1e-6

    maxcols = [int((avail_w + gap) // (w[i] + gap)) if w[i] + gap > 0 else 0 for i in range(n)]
    rows = [int((avail_h + gap) // (h[i] + gap)) if h[i] + gap > 0 else 0 for i in range(n)]

    def width_of(a):
        total = 0.0
        active = 0
        for i, c in enumerate(a):
            if c > 0:
                total += c * w[i] + (c - 1) * gap
                active += 1
        if active > 1:
            total += (active - 1) * gap
        return total

    alloc = [0] * n
    for i in range(n):
        if maxcols[i] >= 1 and rows[i] > 0:
            alloc[i] = 1
    while width_of(alloc) > avail_w + 1e-6 and any(alloc):
        idx = max((i for i in range(n) if alloc[i] > 0), key=lambda i: w[i])
        alloc[idx] -= 1
    while True:
        best_i, best_gain = None, 0.0
        for i in range(n):
            if alloc[i] >= maxcols[i] or rows[i] == 0:
                continue
            trial = alloc[:]
            trial[i] += 1
            if width_of(trial) <= avail_w + 1e-6:
                gain = rows[i] * w[i] * h[i]
                if gain > best_gain:
                    best_gain, best_i = gain, i
        if best_i is None:
            break
        alloc[best_i] += 1

    placements, counts = [], {}
    x = rail
    for i in range(n):
        c = alloc[i]
        if c <= 0:
            counts[kinds[i]["label"]] = 0
            continue
        for r in range(rows[i]):
            for cc in range(c):
                placements.append({
                    "x": x + cc * (w[i] + gap), "y": rail + r * (h[i] + gap),
                    "w": w[i], "h": h[i], "label": kinds[i]["label"],
                    "color": kinds[i]["color"], "rotated": rotated[i], "angle": angs[i],
                    "orig_w": kinds[i]["w"], "orig_h": kinds[i]["h"],
                })
        counts[kinds[i]["label"]] = rows[i] * c
        x += c * w[i] + (c - 1) * gap + gap
    return placements, 0, counts


def _assemble_plan(p, mode, counts, util, views, extra, wpnl_w, wpnl_h, sheet_w, sheet_h):
    overall = util.get("overall", 0)
    full_area = wpnl_w * wpnl_h
    free_mm = 0.0
    if p.get("wp_nl_option"):
        free_mm = (_num(p.get("panel_free_inch"), PANEL_FREE_INCH) or 0.0) * INCH_TO_MM
    eff_w = max(wpnl_w - free_mm, 1e-3)
    eff_h = max(wpnl_h - free_mm, 1e-3)
    panel_util = util.get("panel", util.get("wpnl", 0))
    charged_util = panel_util * full_area / (eff_w * eff_h) if eff_w * eff_h else 0
    charged_util = min(charged_util, 1.0)
    cost_index = round(1.0 / charged_util, 3) if charged_util else None
    copper = None
    cf = p.get("copper_foil") or {}
    if cf.get("enabled"):
        copper = {
            "sheet_area_cm2": round(sheet_w * sheet_h / 100.0, 2),
            "copper_area_cm2": round(sheet_w * sheet_h / 100.0 * overall, 2),
            "utilization": round(overall, 3),
        }
    issues = []
    if overall < _num(p.get("min_utilization"), 0.7):
        issues.append(make_issue(code="PNL-02", category="拼板", rule="板材利用率", severity="中",
                                 description=f"整体板材利用率 {overall*100:.1f}% 偏低",
                                 measured=round(overall, 3), required=_num(p.get("min_utilization"), 0.7),
                                 unit="ratio", suggestion="调整排列/旋转/凑表值以提升利用率"))
    if util.get("sheet", 1) <= 0:
        issues.append(make_issue(code="PNL-03", category="拼板", rule="工作稿/大板", severity="高",
                                 description=f"工作稿 {wpnl_w:.1f}x{wpnl_h:.1f}mm 无法排入大板 "
                                             f"{sheet_w:.1f}x{sheet_h:.1f}mm",
                                 suggestion="增大大板 sheet_w/sheet_h 或选择更小 WPNL 选项"))
    rec = (f"{mode} 排版: 工作稿利用率 {panel_util*100:.1f}% (计入成本 {charged_util*100:.1f}%) "
           f"整体 {overall*100:.1f}%; 相对成本指数 {cost_index}")
    mounts = {k: round(v, 4) for k, v in util.items()}
    mounts["panel_charged"] = round(charged_util, 4)
    return {
        "ok": True, "mode": mode, "arrangement": p.get("arrangement", "grid"),
        "containers": {k: v["container"] for k, v in views.items()},
        "views": views, "counts": counts,
        "utilization": mounts,
        "panel": {
            "option": p.get("wp_nl_option") or "",
            "free_mm": round(free_mm, 3),
            "full_mm": [round(wpnl_w, 3), round(wpnl_h, 3)],
            "charged_mm": [round(eff_w, 3), round(eff_h, 3)],
            "full_mil": [to_mil(wpnl_w), to_mil(wpnl_h)],
            "charged_mil": [to_mil(eff_w), to_mil(eff_h)],
            "full_inch": [round(wpnl_w / INCH_TO_MM, 3), round(wpnl_h / INCH_TO_MM, 3)],
        },
        "units": {"input": p.get("units", "mm")},
        "display_unit": p.get("units", "mm"),
        "layout_recommendation": recommend_layout(p),
        "stackup_recommendation": recommend_stackup(p),
        "cost": {"cost_index": cost_index, "copper_foil": copper},
        "features": extra, "issues": issues, "recommendation": rec,
        "params": p,
    }


def pcs_summary(plan: dict) -> dict:
    """单电路板(PCS)尺寸信息: 净尺寸 / 含工艺边外形 / 工艺边 / 连接筋 / 面积"""
    if not plan or not plan.get("ok"):
        return {}
    v = (plan.get("views") or {}).get("pcs") or {}
    board = v.get("board") or []
    cont = v.get("container") or [None, None]
    rail = _num(v.get("rail_mm"), 0.0) or 0.0
    p = plan.get("params") or {}
    rib = _num(p.get("edge_rib_mm"), 2.0) or 2.0
    cnt = int(_num(p.get("rail_rib_count"), 3) or 3)
    bw = board[0] if len(board) > 0 else None
    bh = board[1] if len(board) > 1 else None
    items = v.get("items") or [{}]
    return {
        "board_w": bw, "board_h": bh,
        "board_mil": [to_mil(bw), to_mil(bh)],
        "outer_w": cont[0], "outer_h": cont[1],
        "outer_mil": [to_mil(cont[0]), to_mil(cont[1])],
        "rail_mm": rail, "rib_mm": rib, "rib_count": cnt,
        "rib_sides": p.get("rib_sides", "all"),
        "board_area_cm2": round(bw * bh / 100.0, 3) if bw and bh else None,
        "outer_area_cm2": round(cont[0] * cont[1] / 100.0, 3) if cont[0] and cont[1] else None,
        "unit": v.get("unit", "mm"),
        "outline_points": len(items[0].get("poly") or []) if items else 0,
    }


def _rounded_rect_pts(w: float, h: float, r: float, seg: int = 8):
    pts = []
    for cx, cy, a0, a1 in ((r, r, 180, 270), (w - r, r, 270, 360),
                           (w - r, h - r, 0, 90), (r, h - r, 90, 180)):
        for i in range(seg + 1):
            a = math.radians(a0 + (a1 - a0) * i / seg)
            pts.append((round(cx + r * math.cos(a), 3), round(cy + r * math.sin(a), 3)))
    return pts


def _circle_pts(d: float, seg: int = 64):
    r = d / 2.0
    return [(round(r + r * math.cos(2 * math.pi * i / seg), 3),
             round(r + r * math.sin(2 * math.pi * i / seg), 3)) for i in range(seg)]


PCS_EXAMPLES = [
    "矩形 31 x 31.2",
    "圆角矩形 40 x 25 (R3)",
    "L形 50 x 30 (缺角 20x10)",
    "圆形 Φ30",
]


def make_pcs_example(kind: str) -> dict:
    """生成 PCS 示例: 返回 {board_w, board_h, board_outline}"""
    kind = (kind or "").strip()
    if kind.startswith("圆角"):
        w, h, r = 40.0, 25.0, 3.0
        return {"board_w": w, "board_h": h, "board_outline": _rounded_rect_pts(w, h, r)}
    if kind.startswith("L形"):
        pts = [(0, 0), (50, 0), (50, 20), (30, 20), (30, 30), (0, 30)]
        return {"board_w": 50.0, "board_h": 30.0, "board_outline": pts}
    if kind.startswith("圆形"):
        return {"board_w": 30.0, "board_h": 30.0, "board_outline": _circle_pts(30.0)}
    return {"board_w": 31.0, "board_h": 31.2, "board_outline": None}


def design_panel(params: dict) -> dict:
    p = dict(DEFAULT_PARAMS)
    p.update({k: v for k, v in (params or {}).items() if v is not None})
    mode = str(p.get("mode", "single")).lower()
    arr = normalize_arrangement(p.get("arrangement"))
    if arr == "mixed" and mode in ("single", "1"):
        mode = "mixed" if p.get("mixed_boards") else "ab"
    p["arrangement"] = arr
    if mode in ("single", "1"):
        return _design_single(p)
    if mode in ("ab", "大小拼", "a/b"):
        return _design_multi(p, "ab")
    return _design_multi(p, "mixed")


# ============================================================
# SVG 渲染 (三图)
# ============================================================
def render_view_svg(view: dict, width: int = 520) -> str:
    cw, ch = view["container"]
    scale = width / max(cw, ch) if max(cw, ch) else 1.0
    W = cw * scale + 20
    H = ch * scale + 20
    parts = [f"<svg xmlns='http://www.w3.org/2000/svg' width='{W:.0f}' height='{H:.0f}'>",
             f"<rect x='0' y='0' width='{W:.0f}' height='{H:.0f}' fill='#fafafa'/>",
             f"<rect x='10' y='10' width='{cw*scale:.1f}' height='{ch*scale:.1f}' "
             f"fill='#f0f0f0' stroke='#333' stroke-width='1'/>"]
    title = view.get("title", "")
    parts.append(f"<text x='10' y='{H-2:.0f}' font-size='11' fill='#333'>{title}</text>")

    def tx(x):
        return 10 + x * scale

    def ty(y):
        return 10 + (ch - y) * scale

    for f in view.get("features", []):
        t = f.get("type")
        if t == "vcut":
            dash = "" if f.get("solid") else " stroke-dasharray='5,3'"
            parts.append(f"<line x1='{tx(f['x1']):.1f}' y1='{ty(f['y1']):.1f}' "
                         f"x2='{tx(f['x2']):.1f}' y2='{ty(f['y2']):.1f}' "
                         f"stroke='#c62828' stroke-width='1'{dash}/>")
        elif t == "stamp":
            r = max(0.8, (f.get("d") or 0.5) / 2 * scale)
            parts.append(f"<circle cx='{tx(f['x']):.1f}' cy='{ty(f['y']):.1f}' "
                         f"r='{r:.1f}' fill='#555'/>")
        elif t == "midcut":
            parts.append(f"<line x1='{tx(f['x1']):.1f}' y1='{ty(f['y1']):.1f}' "
                         f"x2='{tx(f['x2']):.1f}' y2='{ty(f['y2']):.1f}' stroke='#00695c' "
                         f"stroke-width='{max(1.0, (f.get('w') or 2.0) * scale):.1f}' "
                         f"stroke-dasharray='8,4'/>")
        elif t == "impedance":
            parts.append(f"<rect x='{tx(f['x']-f['w']/2):.1f}' y='{ty(f['y']+f['h']):.1f}' "
                         f"width='{f['w']*scale:.1f}' height='{f['h']*scale:.1f}' "
                         f"fill='#7e57c2' fill-opacity='0.6'/>")
        elif t == "break_off":
            parts.append(f"<rect x='{tx(0):.1f}' y='{ty(f['h']):.1f}' width='{f['w']*scale:.1f}' "
                         f"height='{f['h']*scale:.1f}' fill='none' stroke='#0a7a3a' stroke-width='1.5'/>")
        elif t == "mark":
            parts.append(f"<circle cx='{tx(f['x']):.1f}' cy='{ty(f['y']):.1f}' r='3' fill='#ffd966' stroke='#bf8f00'/>")
        elif t == "tooling":
            parts.append(f"<circle cx='{tx(f['x']):.1f}' cy='{ty(f['y']):.1f}' r='2.5' fill='#8ea9db' stroke='#2f5496'/>")
        elif t == "rib":
            parts.append(f"<rect x='{tx(f['x']):.1f}' y='{ty(f['y']+f['h']):.1f}' "
                         f"width='{f['w']*scale:.1f}' height='{f['h']*scale:.1f}' fill='#0a7a3a'/>")
        elif t == "rail":
            parts.append(f"<rect x='{tx(f['x']):.1f}' y='{ty(f['y']+f['h']):.1f}' "
                         f"width='{f['w']*scale:.1f}' height='{f['h']*scale:.1f}' "
                         f"fill='#bdbdbd' fill-opacity='0.85'/>")
        elif t == "direction":
            pts = f"{tx(f['x']):.1f},{ty(f['y']):.1f} {tx(f['x']+3):.1f},{ty(f['y']):.1f} " \
                  f"{tx(f['x']):.1f},{ty(f['y']+3):.1f}"
            parts.append(f"<polygon points='{pts}' fill='#c00000'/>")
        elif t == "leftover":
            parts.append(f"<rect x='{tx(f['x']):.1f}' y='{ty(f['y']+f['h']):.1f}' "
                         f"width='{f['w']*scale:.1f}' height='{f['h']*scale:.1f}' "
                         f"fill='#d9d9d9' fill-opacity='0.5' stroke='#999' "
                         f"stroke-dasharray='4,3'/>")
        elif t == "dim":
            x1, y1, x2, y2 = tx(f["x1"]), ty(f["y1"]), tx(f["x2"]), ty(f["y2"])
            txt = format_dim(f.get("value"), view.get("unit", "mm"))
            if f.get("label"):
                txt = f"{f['label']} {txt}"
            if f.get("orient") == "v":
                xx = x1 - 16
                parts.append(f"<line x1='{xx:.1f}' y1='{y1:.1f}' x2='{xx:.1f}' y2='{y2:.1f}' stroke='#c00' stroke-width='0.8'/>")
                parts.append(f"<line x1='{xx-4:.1f}' y1='{y1:.1f}' x2='{xx+4:.1f}' y2='{y1:.1f}' stroke='#c00'/>")
                parts.append(f"<line x1='{xx-4:.1f}' y1='{y2:.1f}' x2='{xx+4:.1f}' y2='{y2:.1f}' stroke='#c00'/>")
                my = (y1 + y2) / 2
                parts.append(f"<text x='{xx-4:.1f}' y='{my:.1f}' font-size='10' fill='#c00' "
                             f"text-anchor='middle' transform='rotate(-90 {xx-4:.1f} {my:.1f})'>{txt}</text>")
            else:
                yy = y1 + 16
                parts.append(f"<line x1='{x1:.1f}' y1='{yy:.1f}' x2='{x2:.1f}' y2='{yy:.1f}' stroke='#c00' stroke-width='0.8'/>")
                parts.append(f"<line x1='{x1:.1f}' y1='{yy-4:.1f}' x2='{x1:.1f}' y2='{yy+4:.1f}' stroke='#c00'/>")
                parts.append(f"<line x1='{x2:.1f}' y1='{yy-4:.1f}' x2='{x2:.1f}' y2='{yy+4:.1f}' stroke='#c00'/>")
                parts.append(f"<text x='{(x1+x2)/2:.1f}' y='{yy+12:.1f}' font-size='10' fill='#c00' "
                             f"text-anchor='middle'>{txt}</text>")

    for it in view.get("items", []):
        if it.get("hidden"):
            continue
        x, y, w, h = it["x"], it["y"], it["w"], it["h"]
        label = it.get("label", "")
        color = it.get("color", "#2f7ed8")
        angle = it.get("angle", 0.0) or 0.0
        ow = it.get("orig_w") or w
        oh = it.get("orig_h") or h
        poly = it.get("poly")
        frame = it.get("frame")
        stroke = "#000000" if frame else color
        if poly:
            pts = " ".join(f"{tx(px):.1f},{ty(py):.1f}" for px, py in poly)
            parts.append(f"<polygon points='{pts}' fill='{'none' if frame else color}' "
                         f"{'' if frame else 'fill-opacity=\"0.55\"'} stroke='{stroke}' stroke-width='1'/>")
        elif abs(angle % 360) > 1e-6:
            cx, cy = x + w / 2, y + h / 2
            pts = " ".join(f"{tx(px):.1f},{ty(py):.1f}"
                           for px, py in rotated_corners(cx, cy, ow, oh, angle))
            parts.append(f"<polygon points='{pts}' fill='{'none' if frame else color}' "
                         f"{'' if frame else 'fill-opacity=\"0.55\"'} stroke='{stroke}' stroke-width='1'/>")
        else:
            parts.append(f"<rect x='{tx(x):.1f}' y='{ty(y+h):.1f}' width='{w*scale:.1f}' "
                         f"height='{h*scale:.1f}' fill='{'none' if frame else color}' "
                         f"{'' if frame else 'fill-opacity=\"0.55\"'} stroke='{stroke}' stroke-width='1'/>")
        if label and w * scale > 10 and abs(angle % 360) < 1e-6 and not poly:
            parts.append(f"<text x='{tx(x+w/2):.1f}' y='{ty(y+h/2):.1f}' font-size='10' "
                         f"text-anchor='middle' fill='{stroke}'>{label}</text>")
        if it.get("mirror"):
            parts.append(f"<line x1='{tx(x):.1f}' y1='{ty(y):.1f}' x2='{tx(x+w):.1f}' "
                         f"y2='{ty(y+h):.1f}' stroke='#c00000' stroke-width='1.2'/>")
    parts.append("</svg>")
    return "".join(parts)


def render_plan_svgs(plan: dict, width: int = 520) -> Dict[str, str]:
    if not plan.get("ok"):
        return {"pcs": "", "array": "", "panel": ""}
    views = plan["views"]
    return {k: render_view_svg(views[k], width) for k in ("pcs", "array", "panel")}