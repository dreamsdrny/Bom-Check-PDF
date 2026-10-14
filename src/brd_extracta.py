"""
BRD-AI: 通过 Cadence extracta 无界面提取 .brd 几何/网表/DRC
=============================================================
Cadence Allegro 自带命令行工具 extracta.exe, 可在**不打开 GUI**的情况下从 .brd
批量提取数据。本模块用它实现「直接选 .brd -> DFM 报告」的全自动流程:

  视图 (extracta view)          -> 用途
  ------------------------------------------------------------------
  COMPONENT_PIN                 -> 网表: net -> 引脚(refdes.pin) 含 XY
  COMPOSITE_PAD (VIA CLASS)     -> 过孔: XY / 网络 / padstack / 起止层
  COMPOSITE_PAD (非VIA)         -> 焊盘: XY / 网络 / padstack / 测试点
  LAYER                         -> 叠层: 导体/介质厚度 (求板厚)
  NET                           -> 每网络平均线宽 / 过孔数
  CONNECTIVITY (未连ratsnest)   -> 开路 Open (Allegro 连通性, 含 XY)
  COMPONENT                     -> 器件: XY / 旋转 / 面 / 封装
  GEOMETRY (ETCH)               -> 走线段: 起止 XY / 层 / 网络
  GEOMETRY (BOARD OUTLINE)      -> 板框轮廓
  DRC_ERROR                     -> Allegro DRC 违规 (实测/要求/XY/层)
  PAD_DEF2                      -> padstack: 孔径 / 焊盘尺寸 / 类别 / 镀通

输出统一换算为 mm, 组装成与 SKILL 导出 (src.skill_export) 相同的 JSON schema,
从而复用 BrdDesign / dfm_geometry / dfm_netlist / brd_dfm / dfm_report。

坐标单位从 extracta 的 J 记录 (job header) 读取, 常见为 mils。
"""
import os
import re
import subprocess
from typing import Dict, List, Optional, Tuple

MIL_TO_MM = 0.0254

# ---- 视图 cmdfile (有字段视图, 一次调用) ----
CMD_FIELD_VIEWS = """\
COMPONENT_PIN
  NET_NAME
  REFDES
  PIN_NUMBER
  PIN_X
  PIN_Y
END
#
COMPOSITE_PAD
CLASS="VIA CLASS"
  VIA_X
  VIA_Y
  PAD_STACK_NAME
  NET_NAME
  START_LAYER_NAME
  END_LAYER_NAME
END
#
COMPOSITE_PAD
CLASS!="VIA CLASS"
  CLASS
  REFDES
  PIN_NUMBER
  NET_NAME
  PIN_X
  PIN_Y
  PAD_STACK_NAME
  TEST_POINT
END
#
LAYER
  LAYER_SORT
  LAYER_SUBCLASS
  LAYER_CONDUCTOR
  LAYER_THICKNESS
  LAYER_ARTWORK
END
#
NET
  NET_NAME
  NET_ETCH_WIDTH_AVERAGE
  NET_VIA_COUNT
END
#
CONNECTIVITY
  NET_NAME != ''
  GRAPHIC_DATA_NAME = 'RATSNEST'
  RAT_CONNECTED = 'NO'
  NET_NAME
  NODE_1_NUMBER
  NODE_2_NUMBER
  REFDES
  PIN_NUMBER
  PIN_X
  PIN_Y
END
#
COMPONENT
  REFDES
  COMP_PACKAGE
  SYM_NAME
  SYM_X
  SYM_Y
  SYM_ROTATE
  SYM_MIRROR
END
#
GEOMETRY
CLASS=ETCH
  CLASS
  SUBCLASS
  GRAPHIC_DATA_NAME
  GRAPHIC_DATA_1
  GRAPHIC_DATA_2
  GRAPHIC_DATA_3
  GRAPHIC_DATA_4
  NET_NAME
END
#
GEOMETRY
CLASS=BOARD GEOMETRY
SUBCLASS=OUTLINE
  OR
CLASS=SUBSTRATE GEOMETRY
SUBCLASS=OUTLINE
  CLASS
  SUBCLASS
  GRAPHIC_DATA_NAME
  GRAPHIC_DATA_1
  GRAPHIC_DATA_2
  GRAPHIC_DATA_3
  GRAPHIC_DATA_4
END
"""

CMD_DRC = "DRC_ERROR\n"
CMD_PADDEF = "PAD_DEF2\n"

# 字段视图 -> 输出文件名 (顺序与 cmdfile 中视图顺序一致)
FIELD_VIEW_FILES = ["pin", "via", "pad", "layer", "net", "ucon",
                    "comp", "etch", "outline"]


# ============================================================
# 定位 extracta.exe
# ============================================================
def find_extracta() -> Optional[str]:
    """在 CDSROOT / 常见 Cadence 安装路径 / PATH 中查找 extracta.exe"""
    cands: List[str] = []
    cdsroot = os.environ.get("CDSROOT")
    if cdsroot:
        cands.append(os.path.join(cdsroot, "tools", "bin", "extracta.exe"))
    for base in (r"C:\Cadence", r"D:\Cadence", r"C:\Program Files\Cadence",
                 r"C:\Program Files (x86)\Cadence"):
        if os.path.isdir(base):
            try:
                for spb in sorted(os.listdir(base), reverse=True):
                    cands.append(os.path.join(base, spb, "tools", "bin", "extracta.exe"))
            except OSError:
                pass
    for c in cands:
        if os.path.isfile(c):
            return c
    # PATH
    for p in os.environ.get("PATH", "").split(os.pathsep):
        c = os.path.join(p, "extracta.exe")
        if os.path.isfile(c):
            return c
    return None


# ============================================================
# 单位 / 数值
# ============================================================
def _unit_scale(token: str) -> float:
    t = (token or "").strip().lower()
    if t in ("mil", "mils", "thou"):
        return MIL_TO_MM
    if t in ("mm", "millimeter", "millimeters"):
        return 1.0
    if t in ("inch", "inches", "in"):
        return 25.4
    if t in ("micron", "microns", "um"):
        return 0.001
    if t in ("cm", "centimeter", "centimeters"):
        return 10.0
    if t in ("m", "meter", "meters"):
        return 1000.0
    return MIL_TO_MM   # extracta 默认多为 mils


def _f(v) -> Optional[float]:
    try:
        s = str(v).strip()
        if s == "":
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


_THICK_RE = re.compile(r"([-+]?\d*\.?\d+)\s*([A-Za-z]*)")


def _parse_thickness(s) -> Optional[float]:
    """"1.500000 mil" -> mm"""
    if s is None:
        return None
    m = _THICK_RE.search(str(s))
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2).lower()
    return val * _unit_scale(unit or "mil")


# ============================================================
# extracta 输出解析 (A!/J!/S! 记录)
# ============================================================
def _read_table(path: str) -> dict:
    """解析 extracta 输出文件 -> {fields, records, units, scale, extents}"""
    fields: List[str] = []
    records: List[dict] = []
    units = "mils"
    extents = None
    if not os.path.isfile(path):
        return {"fields": fields, "records": records, "units": units,
                "scale": MIL_TO_MM, "extents": extents}
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.rstrip("\n").rstrip("\r")
            if not line:
                continue
            parts = line.split("!")
            tag = parts[0]
            if tag == "A":
                fields = [p for p in parts[1:] if p != ""] or \
                         [p for p in parts[1:-1]]
                fields = [p for p in parts[1:]]
                if fields and fields[-1] == "":
                    fields.pop()
            elif tag == "J":
                # J!<brd>!!<minx>!<miny>!<maxx>!<maxy>!<acc>!<units>!<name>!...
                if len(parts) > 8:
                    units = parts[8] or units
                try:
                    extents = [float(parts[3]), float(parts[4]),
                               float(parts[5]), float(parts[6])]
                except (ValueError, IndexError):
                    extents = None
            elif tag == "S":
                vals = parts[1:]
                if vals and vals[-1] == "":
                    vals.pop()
                rec = {}
                for i, name in enumerate(fields):
                    rec[name] = vals[i] if i < len(vals) else ""
                records.append(rec)
    return {"fields": fields, "records": records, "units": units,
            "scale": _unit_scale(units), "extents": extents}


# ============================================================
# 运行 extracta
# ============================================================
def _run(extracta: str, brd: str, cmdfile: str, outs: List[str],
         workdir: str, timeout: int = 900) -> bool:
    args = [extracta, "-q", "-w", brd, cmdfile] + outs
    try:
        subprocess.run(args, cwd=workdir, stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=timeout, check=False)
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def run_extracta(brd_path: str, workdir: str,
                 extracta: Optional[str] = None) -> Dict[str, str]:
    """运行 extracta (3 次调用), 返回 {视图key: 输出文件路径}"""
    extracta = extracta or find_extracta()
    if not extracta:
        raise RuntimeError(
            "未找到 extracta.exe (Cadence Allegro 命令行工具)。"
            "请确认已安装 Allegro 且 CDSROOT 已设置, 或改用 SKILL 导出流程。")
    if not os.path.isfile(brd_path):
        raise FileNotFoundError(brd_path)
    os.makedirs(workdir, exist_ok=True)

    def wcmd(name, text):
        p = os.path.join(workdir, name)
        with open(p, "w", encoding="ascii", errors="ignore") as f:
            f.write(text)
        return p

    paths: Dict[str, str] = {}
    # 1) 字段视图
    cf1 = wcmd("_extract_field.cmd", CMD_FIELD_VIEWS)
    outs1 = [os.path.join(workdir, f"x_{k}.txt") for k in FIELD_VIEW_FILES]
    _run(extracta, brd_path, cf1, outs1, workdir)
    for k, o in zip(FIELD_VIEW_FILES, outs1):
        paths[k] = o
    # 2) DRC_ERROR
    cf2 = wcmd("_extract_drc.cmd", CMD_DRC)
    out2 = os.path.join(workdir, "x_drc.txt")
    _run(extracta, brd_path, cf2, [out2], workdir)
    paths["drc"] = out2
    # 3) PAD_DEF2
    cf3 = wcmd("_extract_paddef.cmd", CMD_PADDEF)
    out3 = os.path.join(workdir, "x_paddef.txt")
    _run(extracta, brd_path, cf3, [out3], workdir)
    paths["paddef"] = out3
    return paths


# ============================================================
# padstack 定义解析
# ============================================================
def parse_padstacks(paddef_table: dict, scale: float) -> Dict[str, dict]:
    """PAD_DEF2 -> {padstack_name: {drill_mm, pad_mm, pad_w_mm, pad_h_mm,
                                     plating, category, is_smd}}"""
    result: Dict[str, dict] = {}
    for rec in paddef_table.get("records", []):
        name = (rec.get("PAD_NAME") or "").strip()
        if not name:
            continue
        layer = (rec.get("LAYER") or "").strip()
        cat = (rec.get("PAD_STACK_CATEGORY") or "").strip()
        viaflag = (rec.get("VIAFLAG") or "").strip().lower()
        d = result.setdefault(name, {
            "drill_mm": None, "pad_mm": None, "pad_w_mm": None, "pad_h_mm": None,
            "plating": "", "category": cat, "is_smd": False,
        })
        if cat:
            d["category"] = cat
        if viaflag == "v" or cat.lower() in ("smd", "surface_pin"):
            d["is_smd"] = True
        shape1 = rec.get("PADSHAPE1")
        pw = _f(rec.get("PADWIDTH"))
        ph = _f(rec.get("PADHGHT"))
        if layer == "~DRILL":
            drill = _f(shape1)               # ~DRILL: PADSHAPE1 = 孔径
            if drill is not None:
                d["drill_mm"] = round(drill * scale, 4)
            d["plating"] = (rec.get("PADSHAPENAME") or "").strip()
        elif layer in ("TOP", "BOTTOM") or (layer and not layer.startswith("~")):
            # 铜层焊盘尺寸 (取首个非零)
            if pw and ph and d["pad_w_mm"] is None:
                d["pad_w_mm"] = round(pw * scale, 4)
                d["pad_h_mm"] = round(ph * scale, 4)
                d["pad_mm"] = round(min(pw, ph) * scale, 4)
    return result


# ============================================================
# 组装为统一 design dict (与 SKILL 导出同 schema)
# ============================================================
def build_design_dict(brd_path: str, paths: Dict[str, str]) -> dict:
    tables = {k: _read_table(p) for k, p in paths.items()}
    # 单位比例: 以 pin 表为准 (各表一致)
    scale = MIL_TO_MM
    units = "mils"
    for k in ("pin", "via", "comp", "etch"):
        t = tables.get(k)
        if t and t.get("records") is not None and (t.get("scale")):
            scale = t["scale"]
            units = t["units"]
            break

    padstacks = parse_padstacks(tables.get("paddef", {}), scale)

    # ---- 叠层 / 板厚 ----
    stackup = []
    thickness_total = 0.0
    conductor_layers = []
    diel_idx = 0
    for rec in tables.get("layer", {}).get("records", []):
        sub = (rec.get("LAYER_SUBCLASS") or "").strip()
        cond = (rec.get("LAYER_CONDUCTOR") or "").strip().upper() == "YES"
        thk = _parse_thickness(rec.get("LAYER_THICKNESS"))
        sort = (rec.get("LAYER_SORT") or "").strip()
        if not sub:
            # 介质层通常无 subclass 名, 但仍需计入板厚
            if thk and thk > 0:
                diel_idx += 1
                stackup.append({"layer": f"DIELECTRIC_{sort or diel_idx}",
                                "type": "dielectric",
                                "thickness_mm": round(thk, 4)})
                thickness_total += thk
            continue
        stackup.append({"layer": sub,
                        "type": "signal" if cond else "dielectric",
                        "thickness_mm": round(thk, 4) if thk else None})
        if thk:
            thickness_total += thk
        if cond:
            conductor_layers.append(sub)

    # ---- 网络 + 平均线宽 ----
    net_width_mm: Dict[str, float] = {}
    net_via_count: Dict[str, int] = {}
    for rec in tables.get("net", {}).get("records", []):
        nm = (rec.get("NET_NAME") or "").strip()
        if not nm:
            continue
        w = _f(rec.get("NET_ETCH_WIDTH_AVERAGE"))
        if w is not None and w > 0:
            net_width_mm[nm] = round(w * scale, 4)
        vc = _f(rec.get("NET_VIA_COUNT"))
        if vc is not None:
            net_via_count[nm] = int(vc)

    # ---- 网表 (net -> nodes) ----
    nets_map: Dict[str, List[dict]] = {}
    for rec in tables.get("pin", {}).get("records", []):
        nm = (rec.get("NET_NAME") or "").strip()
        x = _f(rec.get("PIN_X")); y = _f(rec.get("PIN_Y"))
        if not nm or x is None or y is None:
            continue
        nets_map.setdefault(nm, []).append({
            "refdes": (rec.get("REFDES") or "").strip(),
            "pin": (rec.get("PIN_NUMBER") or "").strip(),
            "x": round(x * scale, 4), "y": round(y * scale, 4),
            "layer": "",
        })

    # ---- 器件 (refdes -> side) ----
    components = []
    comp_side: Dict[str, str] = {}
    for rec in tables.get("comp", {}).get("records", []):
        ref = (rec.get("REFDES") or "").strip()
        x = _f(rec.get("SYM_X")); y = _f(rec.get("SYM_Y"))
        mirror = (rec.get("SYM_MIRROR") or "").strip().upper()
        side = "bottom" if mirror in ("YES", "TRUE", "Y") else "top"
        if ref:
            comp_side[ref] = side
        components.append({
            "refdes": ref,
            "package": (rec.get("COMP_PACKAGE") or rec.get("SYM_NAME") or "").strip(),
            "x": round(x * scale, 4) if x is not None else None,
            "y": round(y * scale, 4) if y is not None else None,
            "rotation": _f(rec.get("SYM_ROTATE")) or 0.0,
            "side": side,
            "pins": None,
        })

    # ---- 过孔 ----
    vias = []
    for rec in tables.get("via", {}).get("records", []):
        x = _f(rec.get("VIA_X")); y = _f(rec.get("VIA_Y"))
        if x is None or y is None:
            continue
        psn = (rec.get("PAD_STACK_NAME") or "").strip()
        ps = padstacks.get(psn, {})
        sl = (rec.get("START_LAYER_NAME") or "").strip()
        el = (rec.get("END_LAYER_NAME") or "").strip()
        cat = (ps.get("category") or "").lower()
        if cat in ("uvia", "microvia") or "uv ia" in cat:
            vtype = "microvia"
        elif sl and el and sl != el and (sl not in (conductor_layers[:1]) or
                                         el not in (conductor_layers[-1:])):
            vtype = "blind/buried"
        else:
            vtype = "through"
        vias.append({
            "net": (rec.get("NET_NAME") or "").strip(),
            "x": round(x * scale, 4), "y": round(y * scale, 4),
            "drill_mm": ps.get("drill_mm"),
            "pad_mm": ps.get("pad_mm"),
            "padstack": psn,
            "start_layer": sl, "end_layer": el, "type": vtype,
        })

    # ---- 焊盘 + 测试点 ----
    pads = []
    testpoints = []
    for rec in tables.get("pad", {}).get("records", []):
        x = _f(rec.get("PIN_X")); y = _f(rec.get("PIN_Y"))
        if x is None or y is None:
            continue
        ref = (rec.get("REFDES") or "").strip()
        pin = (rec.get("PIN_NUMBER") or "").strip()
        net = (rec.get("NET_NAME") or "").strip()
        psn = (rec.get("PAD_STACK_NAME") or "").strip()
        ps = padstacks.get(psn, {})
        tp = (rec.get("TEST_POINT") or "").strip()
        layer = ""
        side = comp_side.get(ref)
        if ps.get("is_smd"):
            layer = "BOTTOM" if side == "bottom" else "TOP"
        pads.append({
            "refdes": ref, "pin": pin, "net": net,
            "x": round(x * scale, 4), "y": round(y * scale, 4),
            "layer": layer, "padstack": psn,
            "is_smd": bool(ps.get("is_smd")),
            "pad_w": ps.get("pad_w_mm"), "pad_h": ps.get("pad_h_mm"),
            "drill_mm": ps.get("drill_mm"),
        })
        if tp or ref.upper().startswith("TP"):
            testpoints.append({
                "net": net, "refdes": ref, "pin": pin,
                "x": round(x * scale, 4), "y": round(y * scale, 4),
                "layer": layer or (tp if tp in ("TOP", "BOTTOM") else ""),
                "pad_mm": ps.get("pad_mm"),
            })

    # ---- 走线 (etch) ----
    clines = []
    for rec in tables.get("etch", {}).get("records", []):
        gdn = (rec.get("GRAPHIC_DATA_NAME") or "").strip().upper()
        if gdn not in ("LINE", "ARC"):
            continue
        x1 = _f(rec.get("GRAPHIC_DATA_1")); y1 = _f(rec.get("GRAPHIC_DATA_2"))
        x2 = _f(rec.get("GRAPHIC_DATA_3")); y2 = _f(rec.get("GRAPHIC_DATA_4"))
        if None in (x1, y1, x2, y2):
            continue
        net = (rec.get("NET_NAME") or "").strip()
        w = net_width_mm.get(net, 0.15)
        clines.append({
            "net": net,
            "layer": (rec.get("SUBCLASS") or "").strip(),
            "width": w,
            "points": [[round(x1 * scale, 4), round(y1 * scale, 4)],
                       [round(x2 * scale, 4), round(y2 * scale, 4)]],
        })

    # ---- 板框 ----
    outline = []
    for rec in tables.get("outline", {}).get("records", []):
        x1 = _f(rec.get("GRAPHIC_DATA_1")); y1 = _f(rec.get("GRAPHIC_DATA_2"))
        x2 = _f(rec.get("GRAPHIC_DATA_3")); y2 = _f(rec.get("GRAPHIC_DATA_4"))
        if x1 is not None and y1 is not None:
            outline.append([round(x1 * scale, 4), round(y1 * scale, 4)])
        if x2 is not None and y2 is not None:
            outline.append([round(x2 * scale, 4), round(y2 * scale, 4)])

    # ---- 开路 (Allegro 连通性 ratsnest) ----
    opens = []
    for rec in tables.get("ucon", {}).get("records", []):
        x = _f(rec.get("PIN_X")); y = _f(rec.get("PIN_Y"))
        opens.append({
            "net": (rec.get("NET_NAME") or "").strip(),
            "node1": (rec.get("NODE_1_NUMBER") or "").strip(),
            "node2": (rec.get("NODE_2_NUMBER") or "").strip(),
            "refdes": (rec.get("REFDES") or "").strip(),
            "pin": (rec.get("PIN_NUMBER") or "").strip(),
            "x": round(x * scale, 4) if x is not None else None,
            "y": round(y * scale, 4) if y is not None else None,
        })

    # ---- DRC 违规 ----
    drc = []
    for rec in tables.get("drc", {}).get("records", []):
        x = _f(rec.get("DRC_X")); y = _f(rec.get("DRC_Y"))
        tcode = (rec.get("DRC_TYPE_CODE") or "").strip()
        actual = (rec.get("DRC_ACTUAL_VALUE") or "").strip()
        required = (rec.get("DRC_REQUIRED_VALUE") or "").strip()
        cset = (rec.get("DRC_CONSTR_SET_NAME") or "").strip()
        sub = (rec.get("DRC_SUBCLASS") or "").strip()
        text = f"DRC {tcode}".strip()
        if actual or required:
            text += f": 实测 {actual} / 要求 {required}"
        if cset:
            text += f" [{cset}]"
        drc.append({
            "type": tcode or "DRC",
            "severity": "error",
            "x": round(x * scale, 4) if x is not None else None,
            "y": round(y * scale, 4) if y is not None else None,
            "layer": sub,
            "text": text,
        })

    board_name = os.path.splitext(os.path.basename(brd_path))[0]
    design = {
        "format": "brd-dfm-export",
        "version": "1.0",
        "tool": "Cadence extracta (headless)",
        "source": board_name,
        "units": "mm",
        "source_units": units,
        "connectivity_source": "allegro",
        "board": {
            "name": board_name,
            "revision": "",
            "thickness_mm": round(thickness_total, 4) if thickness_total else None,
            "layer_count": len(conductor_layers),
        },
        "outline": outline,
        "stackup": stackup,
        "nets": [{"name": nm, "nodes": nodes} for nm, nodes in
                 sorted(nets_map.items())],
        "components": components,
        "clines": clines,
        "vias": vias,
        "pads": pads,
        "shapes": [],
        "testpoints": testpoints,
        "opens": opens,
        "drc": drc,
        "net_avg_width_mm": net_width_mm,
        # extracta 走线宽度取「网络平均线宽」, 非逐段实测; 几何间距/短路检查据此
        # 排除走线(避免宽电源走线按平均宽膨胀造成的误报), 仅用焊盘/过孔真实尺寸。
        "etch_width_is_average": True,
    }
    return design


def extract_brd(brd_path: str, workdir: Optional[str] = None,
                json_out: Optional[str] = None,
                extracta: Optional[str] = None) -> dict:
    """从 .brd 直接提取 -> 统一 design dict (可选写 JSON)"""
    if workdir is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        name = os.path.splitext(os.path.basename(brd_path))[0]
        workdir = os.path.join(base, "output", "extracta", name)
    paths = run_extracta(brd_path, workdir, extracta=extracta)
    design = build_design_dict(brd_path, paths)
    if json_out:
        import json
        os.makedirs(os.path.dirname(os.path.abspath(json_out)), exist_ok=True)
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump(design, f, ensure_ascii=False, default=str)
        design["_json_out"] = json_out
    design["_workdir"] = workdir
    return design
