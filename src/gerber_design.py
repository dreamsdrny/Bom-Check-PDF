"""
Gerber / ODB++ → 设计模型适配器 (让制造数据也能跑几何 DFM)
=================================================================
思路: 把 Gerber/ODB++ 解析结果转换为与 BRD(extracta/SKILL) **完全相同**的
设计模型 (src.brd_geometry.BrdDesign), 于是可直接复用:
    dfm_geometry.analyze_geometry  线宽/线距/孔环/孔径/纵横比/板边/阻焊桥/测试点
    dfm_netlist.analyze_netlist    开短路 + 网表一致性
    brd_dfm.aggregate_by_rule      按规则汇总 + Valor 式报告 + 交叉定位

映射关系:
    draw / arc          → clines (线段, 带实测线宽)
    region (G36/G37)    → clines (边界折线, 线宽 0 → 只按轮廓算间距)
    flash (D03)         → pads (SMD) 或 vias/pads (有钻孔时)
    NC Drill 孔         → vias (drill_mm; 匹配到焊盘则给 pad_mm 以计算孔环)
    Profile/外形层      → board.outline
    X2 %TO.N/%TO.P      → 逻辑网络与节点 (可判定开路/短路)
    无 X2 属性时        → 由几何连通性自动派生网络 (仍可做间距/孔环/板边)

层识别: 优先 X2 %TF.FileFunction, 其次文件名约定 (AD/Protel/KiCad/Allegro/Eagle)。
"""
import math
import os
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

KIND_COPPER = "copper"
KIND_MASK = "soldermask"
KIND_SILK = "silkscreen"
KIND_PASTE = "paste"
KIND_PROFILE = "profile"
KIND_DRILL = "drill"
KIND_OTHER = "other"

INCH_TO_MM = 25.4

# 文件名约定 → (kind, side, copper_index)
NAME_RULES = [
    (r"(^|[^a-z])npth|nonplated|npt", KIND_DRILL, "", None),
    (r"\.drl$|drill|(^|[^a-z])pth|plated", KIND_DRILL, "", None),
    (r"profile|outline|boardoutline|edge\.cuts|\.gko|\.gm1|\.gm\d|route|rout", KIND_PROFILE, "", None),
    (r"soldermask|smask|sm[_\-]?top|\.gts|\-f\.mask|topmask|mask_top|smt", KIND_MASK, "top", None),
    (r"mask[_\-]?bot|\.gbs|\-b\.mask|botmask|smb", KIND_MASK, "bottom", None),
    (r"silkscreen|silk|legend|\.gto|\-f\.silks|sst|\.plc|overlay", KIND_SILK, "top", None),
    (r"silkscreen[_\-]?bot|\.gbo|\-b\.silks|ssb|\.pls", KIND_SILK, "bottom", None),
    (r"paste|stencil|\.gtp|\-f\.paste|spt", KIND_PASTE, "top", None),
    (r"paste[_\-]?bot|\.gbp|\-b\.paste|spb", KIND_PASTE, "bottom", None),
    (r"\.gtl|\-f\.cu|(^|[^a-z])top|copper[_\-]?top|\.cmp|\.art[_\-]?top", KIND_COPPER, "top", 1),
    (r"\.gbl|\-b\.cu|(^|[^a-z])bot|bottom|copper[_\-]?bot|\.sol", KIND_COPPER, "bottom", None),
    (r"\.gp\d|plane|\.g\d+$|inner|inr\d*|\-in\d+\.cu", KIND_COPPER, "inner", None),
]

ODB_TYPE_MAP = {
    "signal": KIND_COPPER, "power_ground": KIND_COPPER, "mixed": KIND_COPPER,
    "solder_mask": KIND_MASK, "silk_screen": KIND_SILK, "paste_mask": KIND_PASTE,
    "rout": KIND_PROFILE, "profile": KIND_PROFILE, "drill": KIND_DRILL,
    "mask": KIND_MASK, "solder_paste": KIND_PASTE,
}


def identify_layer(data: dict) -> dict:
    """识别一个 Gerber/ODB 层的类型: 优先 X2 TF.FileFunction, 其次文件名"""
    name = str(data.get("name") or os.path.basename(str(data.get("file", ""))))
    ff = str(data.get("file_function") or "").strip()
    low = name.lower()

    if ff:
        parts = [p.strip() for p in ff.split(",") if p.strip()]
        head = parts[0].lower()
        side, idx = "", None
        for p in parts[1:]:
            pl = p.lower()
            if re.fullmatch(r"l\d+", pl):
                idx = int(pl[1:])
            elif pl in ("top", "outer"):
                side = "top"
            elif pl in ("bot", "bottom"):
                side = "bottom"
            elif pl in ("inr", "inner"):
                side = "inner"
        if head == "copper":
            return {"kind": KIND_COPPER, "side": side or "inner", "index": idx,
                    "name": name, "source": "X2"}
        if head in ("soldermask", "solder_mask"):
            return {"kind": KIND_MASK, "side": side, "index": None, "name": name, "source": "X2"}
        if head in ("legend", "silkscreen", "silk"):
            return {"kind": KIND_SILK, "side": side, "index": None, "name": name, "source": "X2"}
        if head in ("solderpaste", "paste"):
            return {"kind": KIND_PASTE, "side": side, "index": None, "name": name, "source": "X2"}
        if head in ("profile", "rout", "outline", "board"):
            return {"kind": KIND_PROFILE, "side": "", "index": None, "name": name, "source": "X2"}
        if head in ("drill", "plated", "nonplated"):
            plated = "npth" not in ff.lower() and "nonplated" not in ff.lower()
            return {"kind": KIND_DRILL, "side": "", "index": None, "name": name,
                    "source": "X2", "plated": plated}

    for pat, kind, side, idx in NAME_RULES:
        if re.search(pat, low):
            return {"kind": kind, "side": side, "index": idx, "name": name, "source": "name"}
    return {"kind": KIND_OTHER, "side": "", "index": None, "name": name, "source": "unknown"}


def _ap_size_mm(data: dict, code) -> Tuple[float, float]:
    """光圈尺寸 (mm): 从 %ADD 定义取, 兼容 inch/mm"""
    ap = (data.get("apertures") or {}).get(code)
    scale = INCH_TO_MM if data.get("unit") == "inch" else 1.0
    if not ap:
        return 0.2, 0.2
    params = [p for p in (ap.get("params") or []) if isinstance(p, (int, float))]
    if not params:
        return 0.2, 0.2
    w = params[0] * scale
    h = (params[1] * scale) if len(params) > 1 and ap.get("shape") in ("R", "O") else w
    return w, h


def _arc_poly(p: dict, seg: int = 16) -> List[Tuple[float, float]]:
    x1, y1 = p.get("x1"), p.get("y1")
    x2, y2 = p.get("x2"), p.get("y2")
    i, j = p.get("i") or 0.0, p.get("j") or 0.0
    if None in (x1, y1, x2, y2):
        return []
    cx, cy = x1 + i, y1 + j
    r = math.hypot(x1 - cx, y1 - cy)
    if r <= 0:
        return [(x1, y1), (x2, y2)]
    a1 = math.atan2(y1 - cy, x1 - cx)
    a2 = math.atan2(y2 - cy, x2 - cx)
    if p.get("cw"):
        while a2 > a1:
            a2 -= 2 * math.pi
    else:
        while a2 < a1:
            a2 += 2 * math.pi
    return [(cx + r * math.cos(a1 + (a2 - a1) * k / seg),
             cy + r * math.sin(a1 + (a2 - a1) * k / seg)) for k in range(seg + 1)]


def _normalize_layers(fab_data: List[dict]) -> Tuple[List[dict], List[dict]]:
    """把 Gerber/ODB++ 统一为 (层列表, 钻孔列表)"""
    layers: List[dict] = []
    drills: List[dict] = []
    for d in fab_data or []:
        kind = d.get("kind")
        if kind == "gerber":
            info = identify_layer(d)
            layers.append({"info": info, "data": d,
                           "prims": d.get("primitives") or []})
        elif kind == "drill":
            for h in d.get("holes", []):
                if h.get("x") is None:
                    continue
                drills.append({"x": h["x"], "y": h["y"], "d": h.get("diameter_mm"),
                               "plating": h.get("plating") or d.get("plating") or "unknown"})
        elif kind == "odbpp":
            lmap = {str(l.get("name")): str(l.get("type", "")) for l in (d.get("layers") or [])}
            by_layer: Dict[str, List[dict]] = defaultdict(list)
            for f in d.get("features") or []:
                by_layer[str(f.get("layer", ""))].append(f)
            for lname, prims in by_layer.items():
                otype = lmap.get(lname, "")
                k = ODB_TYPE_MAP.get(otype.lower(), KIND_COPPER if otype.lower() in ("signal", "power_ground") else KIND_OTHER)
                side = "top" if "top" in lname.lower() else ("bottom" if "bot" in lname.lower() else "inner")
                if k == KIND_DRILL:
                    for f in prims:
                        if f.get("type") == "flash" and f.get("x1") is not None:
                            drills.append({"x": f["x1"], "y": f["y1"], "d": None, "plating": "PTH"})
                    continue
                layers.append({"info": {"kind": k, "side": side, "index": None,
                                        "name": lname, "source": "odb-matrix"},
                               "data": d, "prims": prims})
    return layers, drills


def _copper_layer_name(info: dict, order: int, total: int) -> str:
    side = info.get("side")
    if side == "top":
        return "TOP"
    if side == "bottom":
        return "BOTTOM"
    idx = info.get("index")
    if idx:
        return f"INNER{idx}"
    return f"INNER{order}"


def _sort_copper(coppers: List[dict]) -> List[dict]:
    def key(item):
        info = item["info"]
        side = info.get("side") or "inner"
        prio = {"top": 0, "inner": 1, "bottom": 2}.get(side, 1)
        idx = info.get("index") or 99
        return (prio, idx, info.get("name", ""))
    return sorted(coppers, key=key)


class _DSU:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _derive_nets(els: List[dict], tol: float = 0.02) -> List[int]:
    """无 X2 网络属性时: 用几何连通性派生网络分组 (网格加速)"""
    n = len(els)
    groups = list(range(n))
    if n == 0:
        return groups
    widths = [e["r"] * 2 for e in els if e["r"] > 0]
    cell = max(0.25, sorted(widths)[len(widths) // 2] if widths else 0.25)
    grid: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    for i, e in enumerate(els):
        x0, x1 = int(math.floor(e["minx"] / cell)), int(math.floor(e["maxx"] / cell))
        y0, y1 = int(math.floor(e["miny"] / cell)), int(math.floor(e["maxy"] / cell))
        for gx in range(x0, x1 + 1):
            for gy in range(y0, y1 + 1):
                grid[(gx, gy)].append(i)

    dsu = _DSU(n)
    from src.brd_geometry import seg_seg_dist
    tested = set()
    budget = 4_000_000
    for cell_items in grid.values():
        if len(cell_items) < 2:
            continue
        for a_i in range(len(cell_items)):
            i = cell_items[a_i]
            for b_i in range(a_i + 1, len(cell_items)):
                j = cell_items[b_i]
                if dsu.find(i) == dsu.find(j):
                    continue
                key = (i, j) if i < j else (j, i)
                if key in tested:
                    continue
                tested.add(key)
                if len(tested) > budget:
                    return [dsu.find(k) for k in range(n)]
                ei, ej = els[i], els[j]
                if ei["minx"] > ej["maxx"] or ej["minx"] > ei["maxx"]:
                    continue
                if ei["miny"] > ej["maxy"] or ej["miny"] > ei["maxy"]:
                    continue
                d, _cx, _cy = seg_seg_dist((ei["x1"], ei["y1"]), (ei["x2"], ei["y2"]),
                                           (ej["x1"], ej["y1"]), (ej["x2"], ej["y2"]))
                if d <= ei["r"] + ej["r"] + tol:
                    dsu.union(i, j)
    return [dsu.find(k) for k in range(n)]


def build_design_dict(fab_data: List[dict], board_thickness: Optional[float] = None,
                      board_name: str = "") -> dict:
    """把解析后的制造数据转成 BrdDesign 兼容的 dict"""
    layers, drills = _normalize_layers(fab_data)
    coppers = _sort_copper([L for L in layers if L["info"]["kind"] == KIND_COPPER])
    masks = [L for L in layers if L["info"]["kind"] == KIND_MASK]
    silks = [L for L in layers if L["info"]["kind"] == KIND_SILK]
    profiles = [L for L in layers if L["info"]["kind"] == KIND_PROFILE]

    for i, L in enumerate(coppers, start=2):
        L["layer_name"] = _copper_layer_name(L["info"], i, len(coppers))
    if coppers:
        coppers[0]["layer_name"] = "TOP"
        if len(coppers) > 1:
            coppers[-1]["layer_name"] = "BOTTOM"

    clines: List[dict] = []
    pads: List[dict] = []
    vias: List[dict] = []
    shapes: List[dict] = []
    flashes: List[dict] = []
    outline: List[Tuple[float, float]] = []
    els: List[dict] = []
    has_x2_net = False

    def push_cline(net, layer, width, pts):
        if len(pts) < 2:
            return None
        c = {"net": net or "", "layer": layer, "width": width,
             "points": [[round(x, 4), round(y, 4)] for x, y in pts]}
        clines.append(c)
        return c

    cur = None

    def flush_cur():
        """把当前折线组直接放入 clines (保留对象引用, 便于派生网络回填)"""
        nonlocal cur
        if cur is not None and len(cur["pts"]) >= 2:
            cur["points"] = [[round(x, 4), round(y, 4)] for x, y in cur["pts"]]
            clines.append(cur)
        cur = None

    for L in coppers + masks + silks:
        data = L["data"]
        lname = L.get("layer_name") or L["info"]["name"]
        is_copper = L["info"]["kind"] == KIND_COPPER
        cur = None
        for p in L["prims"]:
            t = p.get("type")
            net = p.get("net") or ""
            if net:
                has_x2_net = True
            if t == "draw":
                if p.get("x1") is None:
                    continue
                w = p.get("width") or 0.0
                if cur and cur["net"] == net and cur["layer"] == lname and \
                        abs(cur["width"] - w) < 1e-9 and cur["pts"] and \
                        abs(cur["pts"][-1][0] - p["x1"]) < 1e-6 and abs(cur["pts"][-1][1] - p["y1"]) < 1e-6:
                    cur["pts"].append((p["x2"], p["y2"]))
                else:
                    flush_cur()
                    cur = {"net": net, "layer": lname, "width": w,
                           "pts": [(p["x1"], p["y1"]), (p["x2"], p["y2"])]}
                if is_copper:
                    r = (w or 0.0) / 2.0
                    els.append({"x1": p["x1"], "y1": p["y1"], "x2": p["x2"], "y2": p["y2"],
                                "r": r, "layer": lname, "net": net, "kind": "seg", "cl": cur,
                                "minx": min(p["x1"], p["x2"]) - r, "maxx": max(p["x1"], p["x2"]) + r,
                                "miny": min(p["y1"], p["y2"]) - r, "maxy": max(p["y1"], p["y2"]) + r})
            elif t == "arc":
                pts = _arc_poly(p)
                if pts:
                    flush_cur()
                    ac = push_cline(net, lname, p.get("width") or 0.0, pts)
                    if is_copper:
                        r = (p.get("width") or 0.0) / 2.0
                        for i2 in range(len(pts) - 1):
                            x1, y1 = pts[i2]
                            x2, y2 = pts[i2 + 1]
                            els.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "r": r,
                                        "layer": lname, "net": net, "kind": "seg", "cl": ac,
                                        "minx": min(x1, x2) - r, "maxx": max(x1, x2) + r,
                                        "miny": min(y1, y2) - r, "maxy": max(y1, y2) + r})
            elif t == "region":
                pts = p.get("points") or []
                flush_cur()
                if len(pts) >= 3:
                    closed = list(pts) + [pts[0]]
                    rc = push_cline(net, lname, 0.0, closed)
                    xs = [q[0] for q in pts]
                    ys = [q[1] for q in pts]
                    shapes.append({"net": net, "layer": lname,
                                   "bbox": [min(xs), min(ys), max(xs), max(ys)]})
                    if is_copper:
                        for i2 in range(len(pts) - 1):
                            x1, y1 = pts[i2]
                            x2, y2 = pts[i2 + 1]
                            els.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "r": 0.0,
                                        "layer": lname, "net": net, "kind": "seg", "cl": rc,
                                        "minx": min(x1, x2), "maxx": max(x1, x2),
                                        "miny": min(y1, y2), "maxy": max(y1, y2)})
            elif t == "flash":
                if p.get("x1") is None:
                    continue
                w, h = _ap_size_mm(data, p.get("aperture"))
                rec = {"x": p["x1"], "y": p["y1"], "w": w, "h": h, "net": net,
                       "layer": lname, "refdes": p.get("refdes") or "", "pin": p.get("pin") or "",
                       "is_copper": is_copper}
                flashes.append(rec)
                if is_copper:
                    r = max(w, h) / 2.0
                    els.append({"x1": p["x1"], "y1": p["y1"], "x2": p["x1"], "y2": p["y1"],
                                "r": r, "layer": lname, "net": net, "kind": "pad",
                                "minx": p["x1"] - r, "maxx": p["x1"] + r,
                                "miny": p["y1"] - r, "maxy": p["y1"] + r, "flash": rec})
        flush_cur()

    # 外形
    for L in profiles:
        for p in L["prims"]:
            if p.get("type") == "region" and p.get("points"):
                if len(p["points"]) > len(outline):
                    outline = [(q[0], q[1]) for q in p["points"]]
            elif p.get("x1") is not None:
                outline.append((p["x1"], p["y1"]))
                if p.get("x2") is not None:
                    outline.append((p["x2"], p["y2"]))
    if not outline:
        xs = [e["minx"] for e in els] + [e["maxx"] for e in els]
        ys = [e["miny"] for e in els] + [e["maxy"] for e in els]
        if xs and ys:
            outline = [(min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys))]

    # 无 X2 网络时: 由几何连通性派生网络, 并回填到走线/焊盘记录
    if not has_x2_net and els:
        groups = _derive_nets(els)
        gmap: Dict[int, str] = {}
        for g in groups:
            if g not in gmap:
                gmap[g] = f"N{len(gmap) + 1:04d}"
        for e, g in zip(els, groups):
            nm = gmap[g]
            e["net"] = nm
            if e.get("cl") is not None:
                e["cl"]["net"] = nm
            if e.get("flash") is not None:
                e["flash"]["net"] = nm

    # 钻孔 ↔ 焊盘匹配 → vias / pads
    tol = 0.05
    used_flash = set()
    first_cu = coppers[0]["layer_name"] if coppers else "TOP"
    last_cu = coppers[-1]["layer_name"] if coppers else "BOTTOM"
    for hole in drills:
        hx, hy, hd = hole["x"], hole["y"], hole.get("d")
        matched = []
        for fi, fl in enumerate(flashes):
            if not fl["is_copper"] or fi in used_flash:
                continue
            if abs(fl["x"] - hx) <= tol and abs(fl["y"] - hy) <= tol:
                matched.append((fi, fl))
        nets = [fl["net"] for _fi, fl in matched if fl["net"]]
        net = max(set(nets), key=nets.count) if nets else ""
        pad_mm = max([max(fl["w"], fl["h"]) for _fi, fl in matched], default=None)
        for fi, fl in matched:
            used_flash.add(fi)
            pads.append({"refdes": fl["refdes"], "pin": fl["pin"], "net": fl["net"] or net,
                         "x": fl["x"], "y": fl["y"], "layer": fl["layer"], "padstack": "",
                         "is_smd": False, "pad_w": fl["w"], "pad_h": fl["h"], "drill_mm": hd})
        plated = str(hole.get("plating") or "").upper()
        vias.append({"net": net, "x": hx, "y": hy, "drill_mm": hd, "pad_mm": pad_mm,
                     "padstack": "", "start_layer": first_cu, "end_layer": last_cu,
                     "type": "npth" if plated == "NPTH" else "through"})
    for fi, fl in enumerate(flashes):
        if fi in used_flash or not fl["is_copper"]:
            continue
        pads.append({"refdes": fl["refdes"], "pin": fl["pin"], "net": fl["net"],
                     "x": fl["x"], "y": fl["y"], "layer": fl["layer"], "padstack": "",
                     "is_smd": True, "pad_w": fl["w"], "pad_h": fl["h"], "drill_mm": None})

    # 网络与节点
    nets: Dict[str, List[dict]] = defaultdict(list)
    for p in pads:
        if p["net"]:
            nets[p["net"]].append({"refdes": p["refdes"], "pin": p["pin"],
                                   "x": p["x"], "y": p["y"], "layer": p["layer"]})
    for v in vias:
        if v["net"]:
            nets[v["net"]].append({"refdes": "", "pin": "", "x": v["x"], "y": v["y"],
                                   "layer": v["start_layer"]})

    stackup = []
    for i, L in enumerate(coppers):
        stackup.append({"layer": L["layer_name"], "type": "copper",
                        "note": L["info"]["name"]})
        if i < len(coppers) - 1:
            stackup.append({"layer": "", "type": "dielectric"})

    return {
        "format": "gerber",
        "units": "mm",
        "source_units": "mm",
        "tool": "Gerber/ODB++ (Evan_DFM adapter)",
        "board": {
            "name": board_name or "Gerber",
            "revision": "",
            "thickness_mm": board_thickness,
            "layer_count": len(coppers),
            "outline": [[round(x, 4), round(y, 4)] for x, y in (outline or [])],
        },
        "outline": [[round(x, 4), round(y, 4)] for x, y in (outline or [])],
        "stackup": stackup,
        "nets": [{"name": k, "nodes": v} for k, v in nets.items()],
        "components": [],
        "clines": clines,
        "vias": vias,
        "pads": pads,
        "shapes": shapes,
        "testpoints": [],
        "drc": [],
        "connectivity_source": "x2" if has_x2_net else "derived",
        "opens": [],
        "net_avg_width_mm": {},
        "etch_width_is_average": False,
        "gerber_meta": {
            "copper_layers": [L["layer_name"] for L in coppers],
            "mask_layers": [L["info"]["name"] for L in masks],
            "silk_layers": [L["info"]["name"] for L in silks],
            "profile_layers": [L["info"]["name"] for L in profiles],
            "drill_count": len(drills),
            "has_x2_net": has_x2_net,
            "layer_identification": {L["info"]["name"]: f"{L['info']['kind']}/{L['info']['source']}"
                                     for L in layers},
        },
    }


def analyze_gerber_dfm(paths, factory: str = "hongban", board_type: str = "rigid",
                       capability_level: str = "standard", copper_um: float = 35.0,
                       copper_oz: Optional[float] = None,
                       board_thickness: Optional[float] = None,
                       cad_nets=None, extracta=None) -> dict:
    """对 Gerber / ODB++ / NC Drill 执行与 BRD 相同的几何 DFM + 开短路 + 网表分析"""
    from src.brd_dfm import analyze_design_model
    from src.brd_geometry import BrdDesign
    from src.fab_data_reader import read_fab_data

    fab = read_fab_data(paths)
    if not fab:
        raise RuntimeError(f"未解析到制造数据: {paths}")
    name = ""
    if isinstance(paths, (list, tuple)) and paths:
        name = os.path.basename(os.path.dirname(str(paths[0]))) or os.path.basename(str(paths[0]))
    elif isinstance(paths, str):
        name = os.path.basename(os.path.normpath(paths))
    dd = build_design_dict(fab, board_thickness=board_thickness, board_name=name or "Gerber")
    design = BrdDesign(dd, source_path=name)
    result = analyze_design_model(design, factory=factory, board_type=board_type,
                                  capability_level=capability_level, copper_um=copper_um,
                                  copper_oz=copper_oz, source_path=name)

    # 可选: CAD 网表 vs Gerber 派生网表
    if cad_nets:
        try:
            from src.dfm_rules import compute_risk_score, summarize_issues
            from src.fab_data_reader import (compare_netlists, netlist_compare_issues,
                                             normalize_cad_nets)
            if isinstance(cad_nets, str):
                import json
                with open(cad_nets, "r", encoding="utf-8-sig") as f:
                    cad_nets = json.load(f)
            fab_nets = {}
            for nname, info in design.nets.items():
                fab_nets[nname] = {f"{nd['refdes']}.{nd['pin']}" for nd in info["nodes"]
                                   if nd.get("refdes")}
            cmp = compare_netlists(normalize_cad_nets(cad_nets), fab_nets)
            extra = netlist_compare_issues(cmp)
            result["issues"] = list(result.get("issues", [])) + extra
            result["issue_summary"] = summarize_issues(result["issues"])
            result["risk"] = compute_risk_score(result["issues"])
            result["fabnet_summary"] = {k: v for k, v in cmp.items()
                                        if k not in ("node_open", "node_short")}
        except Exception as exc:  # noqa: BLE001
            result["fabnet_error"] = str(exc)

    gm = dd.get("gerber_meta", {})
    result["gerber_meta"] = gm
    result.setdefault("info_stats", {}).setdefault("manufacturing", {})
    result["info_stats"]["manufacturing"].update({
        "铜层数": len(gm.get("copper_layers", [])),
        "阻焊层": len(gm.get("mask_layers", [])),
        "丝印层": len(gm.get("silk_layers", [])),
        "钻孔数": gm.get("drill_count"),
        "网络来源": "X2 属性" if gm.get("has_x2_net") else "几何连通性派生",
    })
    if not gm.get("has_x2_net"):
        result.setdefault("warnings", []).append(
            "Gerber 无 X2 网络属性(%TO.N/%TO.P): 网络由几何连通性派生, 可做间距/孔环/板边检查, "
            "但无法判定开路与短路; 建议导出 X2 或提供 CAD 网表")
    return result
