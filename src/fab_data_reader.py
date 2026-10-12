"""
BRD-AI: 制造数据读取与完整性检查 (Gerber / ODB++ / NC Drill)
=============================================================
支持:
  1. Gerber RS-274X 解析 (单位/格式/光圈/D01-D03/G02-G03/G36-G37/X2属性)
  2. Excellon NC Drill 解析 (孔径工具表/坐标/镀通孔判别)
  3. ODB++ (job 目录: matrix / layers/*/features / netlists/cadnet/netlist) 解析
  4. 制造数据完整性检查 (图层缺失/图形畸变风险/坐标偏移/孔径定义错误/网络属性缺失)
  5. 网络表双向比对 (CAD vs FAB -> 开路 Open / 短路 Short)

输出:
  - fab_data: 统一的几何/统计数据结构 (供 DFM 报告与图形交叉定位使用)
  - issues:   与 src.dfm_rules 一致的 DFM 问题结构 (code/severity/measured/x/y/...)
  - 网络比对报告: {net_only_cad, net_only_fab, node_open, node_short, matched, ...}

说明: 解析为 best-effort, 覆盖生产常见写法; 复杂宏/嵌套区域按近似处理。
"""
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from src.dfm_rules import make_issue

MIL_TO_MM = 0.0254
INCH_TO_MM = 25.4

INTEGRITY_ISSUE_PREFIX = "FAB"

_APERTURE_SHAPES = {"C", "R", "O", "P"}


def _num(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coord_to_mm(raw: str, int_digits: int, dec_digits: int, unit: str) -> Optional[float]:
    if raw is None:
        return None
    raw = raw.strip()
    if raw == "":
        return None
    neg = raw.startswith("-")
    digits = raw.lstrip("+-")
    if "." in digits:
        val = _num(digits)
    else:
        val = int(digits) / (10 ** dec_digits) if dec_digits else int(digits)
    if val is None:
        return None
    if neg:
        val = -val
    return val * (INCH_TO_MM if unit == "inch" else 1.0)


# ============================================================
# Gerber RS-274X
# ============================================================
class GerberResult:
    def __init__(self):
        self.unit = "mm"
        self.int_digits = 3
        self.dec_digits = 3
        self.apertures: Dict[int, dict] = {}
        self.primitives: List[dict] = []
        self.bbox = [None, None, None, None]
        self.attributes_present = False
        self.nets: Dict[str, Set[str]] = {}
        self.ap_usage: Dict[int, int] = {}
        self.feature_count = 0
        self.file_name = ""

    def add_point(self, x, y):
        b = self.bbox
        if x is None or y is None:
            return
        b[0] = x if b[0] is None else min(b[0], x)
        b[1] = y if b[1] is None else min(b[1], y)
        b[2] = x if b[2] is None else max(b[2], x)
        b[3] = y if b[3] is None else max(b[3], y)


_NUM = r"[-+]?\d*\.?\d+"


def _parse_aperture(block: str) -> Optional[Tuple[int, dict]]:
    m = re.match(r"(?:ADD)?(\d+)([A-Za-z_$][\w$]*),?(.*)", block)
    if not m:
        return None
    dcode = int(m.group(1))
    shape = m.group(2)
    params_raw = m.group(3)
    params = []
    for p in params_raw.split("X"):
        p = p.strip()
        if p == "":
            continue
        v = _num(p)
        params.append(v if v is not None else p)
    return dcode, {"shape": shape, "params": params, "raw": block}


def parse_gerber(path: str, layer_name: Optional[str] = None) -> dict:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    res = GerberResult()
    res.file_name = layer_name or os.path.basename(path)
    unit = "mm"
    int_digits, dec_digits = 3, 3
    cur_ap: Optional[int] = None
    cur_x = cur_y = 0.0
    interp = 1
    cur_op = 1
    in_region = False
    region_pts: List[Tuple[float, float]] = []
    cur_net = ""
    cur_pin = ""
    cur_refdes = ""
    ap_defined_used: Set[int] = set()

    def finalize_segment(x1, y1, x2, y2, ap_code, kind="draw", width_mm=None):
        prim = {
            "type": kind, "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            "aperture": ap_code, "width": width_mm,
            "net": cur_net or "", "refdes": cur_refdes or "",
            "pin": cur_pin or "", "layer": res.file_name,
        }
        res.primitives.append(prim)
        res.add_point(x1, y1)
        res.add_point(x2, y2)
        res.feature_count += 1

    def ap_width(ap_code):
        ap = res.apertures.get(ap_code)
        if not ap:
            return None
        shape = ap["shape"]
        params = ap["params"]
        if not params:
            return None
        v = params[0]
        if not isinstance(v, (int, float)):
            return None
        w = v
        if shape == "C":
            return w * (INCH_TO_MM if unit == "inch" else 1.0)
        if shape in ("R", "O") and len(params) >= 2 and isinstance(params[1], (int, float)):
            return min(w, params[1]) * (INCH_TO_MM if unit == "inch" else 1.0)
        return w * (INCH_TO_MM if unit == "inch" else 1.0)

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        cmd = line.rstrip("*")
        if line.startswith("%FS"):
            m = re.search(r"X(\d)(\d)Y(\d)(\d)", line)
            if m:
                int_digits = int(m.group(1))
                dec_digits = int(m.group(2))
            continue
        if line.startswith("%MO"):
            if "IN" in line.upper():
                unit = "inch"
            else:
                unit = "mm"
            res.unit = unit
            continue
        if line.startswith("%ADD") or line.startswith("%AD"):
            block = line.strip("%").rstrip("*")
            parsed = _parse_aperture(block)
            if parsed:
                dcode, ap = parsed
                res.apertures[dcode] = ap
            continue
        if line.startswith("%TO"):
            body = line.strip("%").rstrip("*")
            parts = body.split(",")
            if parts and parts[0] == "TO.N":
                cur_net = ",".join(parts[1:]).strip()
                cur_refdes = ""
                cur_pin = ""
                res.attributes_present = True
            elif parts and parts[0] == "TO.P":
                if len(parts) >= 2:
                    cur_refdes = parts[1].strip()
                if len(parts) >= 3:
                    cur_pin = parts[2].strip()
            continue
        if line.startswith("%TD"):
            if line.strip("%").rstrip("*") == "TD":
                cur_net = ""
                cur_refdes = ""
                cur_pin = ""
            continue
        if line.startswith("%LPC"):
            continue
        if line.startswith("%LPD"):
            continue
        if cmd.startswith("G36"):
            in_region = True
            region_pts = []
            continue
        if cmd.startswith("G37"):
            in_region = False
            if region_pts:
                xs = [p[0] for p in region_pts]
                ys = [p[1] for p in region_pts]
                res.primitives.append({
                    "type": "region", "points": list(region_pts),
                    "x1": min(xs) if xs else 0, "y1": min(ys) if ys else 0,
                    "x2": max(xs) if xs else 0, "y2": max(ys) if ys else 0,
                    "width": None, "net": cur_net or "", "refdes": cur_refdes or "",
                    "pin": cur_pin or "", "layer": res.file_name,
                })
                for p in region_pts:
                    res.add_point(p[0], p[1])
                res.feature_count += 1
            region_pts = []
            continue
        md = re.match(r"^D(\d+)$", cmd)
        if md:
            code = int(md.group(1))
            if code >= 10:
                cur_ap = code
            continue
        if re.match(r"^G0[123]", cmd):
            interp = int(cmd[2])
            rest = cmd[3:]
            if rest:
                line = rest
                cmd = rest
            else:
                continue
        if re.match(r"^G7[45]", cmd) or re.match(r"^G0[45]", cmd):
            continue
        m = re.match(r"^(?:G0[123])?X(" + _NUM + r")?Y(" + _NUM + r")?(?:I(" + _NUM + r")?J(" + _NUM + r")?)?D0?([123])$", cmd)
        if m:
            x = _coord_to_mm(m.group(1), int_digits, dec_digits, unit) if m.group(1) else None
            y = _coord_to_mm(m.group(2), int_digits, dec_digits, unit) if m.group(2) else None
            i_off = _coord_to_mm(m.group(3), int_digits, dec_digits, unit) if m.group(3) else None
            j_off = _coord_to_mm(m.group(4), int_digits, dec_digits, unit) if m.group(4) else None
            op = int(m.group(5))
            nx = x if x is not None else cur_x
            ny = y if y is not None else cur_y
            if in_region:
                if op in (1, 2):
                    region_pts.append((nx, ny))
                cur_x, cur_y = nx, ny
                continue
            if op == 3:
                if cur_ap is None:
                    res.primitives.append({"type": "error", "x1": nx, "y1": ny,
                                           "x2": nx, "y2": ny, "error": "flash_no_aperture",
                                           "net": cur_net, "layer": res.file_name})
                else:
                    w = ap_width(cur_ap)
                    res.primitives.append({
                        "type": "flash", "x1": nx, "y1": ny, "x2": nx, "y2": ny,
                        "aperture": cur_ap, "width": w, "net": cur_net or "",
                        "refdes": cur_refdes or "", "pin": cur_pin or "",
                        "layer": res.file_name,
                    })
                    res.ap_usage[cur_ap] = res.ap_usage.get(cur_ap, 0) + 1
                    res.add_point(nx, ny)
                    res.feature_count += 1
            elif op == 1:
                if interp in (2, 3) and (i_off is not None or j_off is not None):
                    w = ap_width(cur_ap) if cur_ap else None
                    res.primitives.append({
                        "type": "arc", "x1": cur_x, "y1": cur_y, "x2": nx, "y2": ny,
                        "i": i_off or 0.0, "j": j_off or 0.0, "cw": interp == 2,
                        "aperture": cur_ap, "width": w, "net": cur_net or "",
                        "refdes": cur_refdes or "", "pin": cur_pin or "",
                        "layer": res.file_name,
                    })
                    if cur_ap is not None:
                        res.ap_usage[cur_ap] = res.ap_usage.get(cur_ap, 0) + 1
                    res.add_point(cur_x, cur_y)
                    res.add_point(nx, ny)
                    cx, cy = cur_x + (i_off or 0), cur_y + (j_off or 0)
                    r = ((cur_x - cx) ** 2 + (cur_y - cy) ** 2) ** 0.5
                    res.add_point(cx - r, cy - r)
                    res.add_point(cx + r, cy + r)
                    res.feature_count += 1
                else:
                    w = ap_width(cur_ap) if cur_ap else None
                    finalize_segment(cur_x, cur_y, nx, ny, cur_ap, "draw", w)
                    if cur_ap is not None:
                        res.ap_usage[cur_ap] = res.ap_usage.get(cur_ap, 0) + 1
            if cur_net:
                if cur_refdes and cur_pin:
                    node = f"{cur_refdes}.{cur_pin}"
                else:
                    node = None
                if node:
                    res.nets.setdefault(cur_net, set()).add(node)
            cur_x, cur_y = nx, ny
            continue
        if re.match(r"^M0?2", line):
            continue

    data = {
        "kind": "gerber",
        "file": path,
        "name": res.file_name,
        "unit": res.unit,
        "apertures": res.apertures,
        "primitives": res.primitives,
        "bbox": res.bbox,
        "feature_count": res.feature_count,
        "attributes_present": res.attributes_present,
        "nets": {k: sorted(v) for k, v in res.nets.items()},
        "min_line_width_mm": _min_line_width(res),
        "min_pad_mm": _min_pad(res),
        "aperture_count": len(res.apertures),
    }
    return data


def _min_line_width(res: GerberResult) -> Optional[float]:
    widths = [p.get("width") for p in res.primitives
              if p.get("type") in ("draw", "arc") and p.get("width")]
    return round(min(widths), 4) if widths else None


def _min_pad(res: GerberResult) -> Optional[float]:
    sizes = []
    for dcode, ap in res.apertures.items():
        params = ap.get("params") or []
        if params and isinstance(params[0], (int, float)):
            size = params[0]
            if ap.get("shape") in ("R", "O") and len(params) >= 2 and isinstance(params[1], (int, float)):
                size = min(params[0], params[1])
            sizes.append(size * (INCH_TO_MM if res.unit == "inch" else 1.0))
    return round(min(sizes), 4) if sizes else None


# ============================================================
# Excellon NC Drill
# ============================================================
def parse_excellon(path: str, plating: Optional[str] = None) -> dict:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    name = os.path.basename(path).upper()
    if plating is None:
        if "NPTH" in name or "NON" in name:
            plating = "NPTH"
        elif "PTH" in name or "DRL" in name:
            plating = "PTH"
        else:
            plating = "unknown"

    unit = "inch"
    decimal_hint = False
    tools: Dict[int, dict] = {}
    holes: List[dict] = []
    cur_tool = None
    in_header = False
    header_done = False

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        up = line.upper()
        if up == "M48":
            in_header = True
            continue
        if up == "%" or up == "M95":
            in_header = False
            header_done = True
            continue
        if up.startswith("METRIC") or up.startswith("M71"):
            unit = "mm"
            continue
        if up.startswith("INCH") or up.startswith("M72"):
            unit = "inch"
            continue
        if up.startswith("FMAT"):
            continue
        m = re.match(r"^T(\d+)C(" + _NUM + r")", line)
        if m:
            d = _num(m.group(2))
            tools[int(m.group(1))] = {
                "diameter": d,
                "diameter_mm": (d * INCH_TO_MM if unit == "inch" else d) if d is not None else None,
            }
            continue
        m = re.match(r"^T(\d+)$", line)
        if m:
            cur_tool = int(m.group(1))
            continue
        if up in ("G90", "G05", "M30", "G54DZ", "G54"):
            continue
        m = re.match(r"^(?:G0[123])?X(" + _NUM + r")Y(" + _NUM + r")", line)
        if m:
            xs, ys = m.group(1), m.group(2)
            if "X" in line and "Y" in line:
                if "." in xs or "." in ys:
                    decimal_hint = True
            x = _parse_drill_coord(xs, unit)
            y = _parse_drill_coord(ys, unit)
            if cur_tool is None:
                cur_tool = 1
            tool = tools.get(cur_tool, {})
            holes.append({
                "x": x, "y": y, "tool": cur_tool,
                "diameter_mm": tool.get("diameter_mm"),
                "plating": plating,
            })
            continue

    bbox = [None, None, None, None]
    for h in holes:
        if h["x"] is None or h["y"] is None:
            continue
        bbox[0] = h["x"] if bbox[0] is None else min(bbox[0], h["x"])
        bbox[1] = h["y"] if bbox[1] is None else min(bbox[1], h["y"])
        bbox[2] = h["x"] if bbox[2] is None else max(bbox[2], h["x"])
        bbox[3] = h["y"] if bbox[3] is None else max(bbox[3], h["y"])

    diameters = [h["diameter_mm"] for h in holes if h.get("diameter_mm")]
    return {
        "kind": "drill",
        "file": path,
        "name": os.path.basename(path),
        "unit": unit,
        "plating": plating,
        "tools": tools,
        "holes": holes,
        "hole_count": len(holes),
        "min_hole_mm": round(min(diameters), 4) if diameters else None,
        "max_hole_mm": round(max(diameters), 4) if diameters else None,
        "bbox": bbox,
    }


def _parse_drill_coord(raw: str, unit: str) -> Optional[float]:
    if raw is None:
        return None
    raw = raw.strip()
    if raw == "":
        return None
    if "." in raw:
        v = _num(raw)
        if v is None:
            return None
        return v * (INCH_TO_MM if unit == "inch" else 1.0)
    neg = raw.startswith("-")
    digits = raw.lstrip("+-")
    if not digits.isdigit():
        return None
    scale = 1000.0 if unit == "mm" else 10000.0
    v = int(digits) / scale
    if neg:
        v = -v
    return v


# ============================================================
# ODB++
# ============================================================
def parse_odbpp(job_dir: str) -> dict:
    matrix_path = os.path.join(job_dir, "matrix", "matrix")
    layers: List[dict] = []
    features: List[dict] = []
    nets: Dict[str, Set[str]] = {}
    units = "inch"

    if os.path.exists(matrix_path):
        with open(matrix_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line.startswith("UNITS"):
                    val = line.split("=")[-1].strip().upper()
                    units = "mm" if val.startswith("MM") else "inch"
                m = re.match(r"^LAYER\s*\{?\s*(\S+)\s+(\S+)", line)
                if m:
                    layers.append({"name": m.group(1), "type": m.group(2)})

    scale = INCH_TO_MM if units == "inch" else 1.0
    layers_dir = os.path.join(job_dir, "layers")
    if os.path.isdir(layers_dir):
        for lname in os.listdir(layers_dir):
            feat_path = os.path.join(layers_dir, lname, "features")
            if not os.path.isfile(feat_path):
                continue
            symbols: Dict[str, dict] = {}
            with open(feat_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    if line.startswith("$"):
                        parts = line[1:].split()
                        if len(parts) >= 2:
                            symbols[parts[0]] = {"type": parts[1], "params": parts[2:]}
                        continue
                    if line.startswith("#"):
                        continue
                    parts = line.split()
                    if not parts:
                        continue
                    rec = parts[0]
                    attrs = {}
                    if ";" in line:
                        body, attr_part = line.split(";", 1)
                        parts = body.split()
                        for a in attr_part.split(";"):
                            if "=" in a:
                                k, v = a.split("=", 1)
                                attrs[k] = v
                    def g(i):
                        try:
                            return float(parts[i]) * scale
                        except (IndexError, ValueError):
                            return None
                    if rec == "L" and len(parts) >= 6:
                        features.append({"type": "draw", "x1": g(1), "y1": g(2),
                                         "x2": g(3), "y2": g(4), "layer": lname,
                                         "net": attrs.get("net", ""), "refdes": attrs.get("c", ""),
                                         "pin": attrs.get("pn", "")})
                    elif rec == "P" and len(parts) >= 3:
                        features.append({"type": "flash", "x1": g(1), "y1": g(2),
                                         "x2": g(1), "y2": g(2), "layer": lname,
                                         "net": attrs.get("net", ""), "refdes": attrs.get("c", ""),
                                         "pin": attrs.get("pn", "")})
                    elif rec == "A" and len(parts) >= 7:
                        features.append({"type": "arc", "x1": g(1), "y1": g(2),
                                         "x2": g(5), "y2": g(6), "layer": lname,
                                         "net": attrs.get("net", ""), "refdes": attrs.get("c", ""),
                                         "pin": attrs.get("pn", "")})

    for net_file in (os.path.join(job_dir, "netlists", "cadnet", "netlist"),
                     os.path.join(job_dir, "netlists", "netlist")):
        if os.path.isfile(net_file):
            nets.update(_parse_odb_netlist(net_file))
            break

    bbox = [None, None, None, None]
    for feat in features:
        for (x, y) in ((feat.get("x1"), feat.get("y1")), (feat.get("x2"), feat.get("y2"))):
            if x is None or y is None:
                continue
            bbox[0] = x if bbox[0] is None else min(bbox[0], x)
            bbox[1] = y if bbox[1] is None else min(bbox[1], y)
            bbox[2] = x if bbox[2] is None else max(bbox[2], x)
            bbox[3] = y if bbox[3] is None else max(bbox[3], y)

    return {
        "kind": "odbpp",
        "file": job_dir,
        "name": os.path.basename(os.path.normpath(job_dir)),
        "unit": units,
        "layers": layers,
        "layer_count": len(layers),
        "features": features,
        "feature_count": len(features),
        "nets": {k: sorted(v) for k, v in nets.items()},
        "bbox": bbox,
        "attributes_present": bool(nets),
    }


def _parse_odb_netlist(path: str) -> Dict[str, Set[str]]:
    nets: Dict[str, Set[str]] = {}
    cur = None
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(";"):
                continue
            if ";" in line and not line.startswith("+"):
                head, *nodes = line.split(";")
                head = head.strip()
                cur = head
                nets.setdefault(cur, set())
                for n in nodes:
                    n = n.strip()
                    if n:
                        nets[cur].add(n.replace("-", "."))
                continue
            m = re.match(r"^(\d+)\s+(\S+)", line)
            if m:
                cur = m.group(2)
                nets.setdefault(cur, set())
                continue
            if line.startswith("+"):
                node = line[1:].strip().replace("-", ".")
                if cur and node:
                    nets[cur].add(node)
                continue
            if cur:
                nets[cur].add(line.replace("-", "."))
    return nets


# ============================================================
# 统一读取
# ============================================================
def read_fab_data(path) -> List[dict]:
    """读取 Gerber/Excellon/ODB++ (目录/文件/路径列表), 返回数据列表"""
    if isinstance(path, (list, tuple)):
        result: List[dict] = []
        for p in path:
            result.extend(read_fab_data(p))
        return result
    result: List[dict] = []
    if os.path.isdir(path):
        if os.path.isdir(os.path.join(path, "matrix")) or \
           os.path.isfile(os.path.join(path, "matrix", "matrix")):
            result.append(parse_odbpp(path))
        else:
            for root, _dirs, files in os.walk(path):
                for fn in sorted(files):
                    result.extend(_read_single(os.path.join(root, fn)))
    else:
        result.extend(_read_single(path))
    return result


def _read_single(path: str) -> List[dict]:
    low = path.lower()
    if low.endswith(".drl") or "drill" in low or "npt" in low or low.endswith(".txt") and "drl" in low:
        try:
            return [parse_excellon(path)]
        except Exception:
            return []
    if low.endswith((".gbr", ".ger", ".art", ".pho")) or _looks_like_gerber(path):
        try:
            return [parse_gerber(path)]
        except Exception:
            return []
    return []


def _looks_like_gerber(path: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(4096)
        return "%FS" in head or "%MO" in head or "%ADD" in head or "G04" in head
    except Exception:
        return False


# ============================================================
# 完整性检查
# ============================================================
def integrity_check(fab_data: List[dict]) -> List[dict]:
    issues: List[dict] = []
    code_counter = [0]

    def add(category, rule, severity, description, x=None, y=None,
            measured=None, required=None, unit="", suggestion="", layer=""):
        code_counter[0] += 1
        issues.append(make_issue(
            code=f"{INTEGRITY_ISSUE_PREFIX}-{code_counter[0]:02d}",
            category=category, rule=rule, severity=severity,
            description=description, x=x, y=y, measured=measured,
            required=required, unit=unit, suggestion=suggestion, layer=layer,
        ))

    if not fab_data:
        add("制造数据", "数据缺失", "危险", "未解析到任何 Gerber/ODB++/NC Drill 制造数据",
            suggestion="确认制造文件路径与格式")
        return issues

    gerbers = [d for d in fab_data if d["kind"] == "gerber"]
    drills = [d for d in fab_data if d["kind"] == "drill"]
    odb = [d for d in fab_data if d["kind"] == "odbpp"]

    if not gerbers and not odb:
        add("制造数据", "图形层缺失", "危险", "缺少线路/图形层 (Gerber 或 ODB++)",
            suggestion="补充 Gerber 或 ODB++ 数据")
    if not drills and not odb:
        add("制造数据", "钻孔层缺失", "危险", "缺少 NC Drill / ODB++ 钻孔数据",
            suggestion="补充 NC Drill 文件")

    seen_layers: Set[str] = set()
    for g in gerbers:
        name = g["name"].lower()
        base = re.sub(r"[^a-z]", "", name)
        if base in seen_layers:
            add("制造数据", "图层重复", "中",
                f"检测到疑似重复图层: {g['name']}", layer=g["name"],
                suggestion="核对叠层/图层命名, 删除重复输出")
        seen_layers.add(base)
        if not g["attributes_present"]:
            add("制造数据", "网络属性缺失", "中",
                f"图层 {g['name']} 无 X2 网络属性 (TO.N/TO.P), 无法进行网络表比对",
                layer=g["name"], suggestion="EDA 导出 Gerber 时启用 X2 属性")
        if g["feature_count"] == 0:
            add("制造数据", "空图层", "高",
                f"图层 {g['name']} 无有效图形", layer=g["name"],
                suggestion="检查该层是否漏输出")
        if g["bbox"][0] is not None and g["bbox"][2] is not None:
            w = g["bbox"][2] - g["bbox"][0]
            h = g["bbox"][3] - g["bbox"][1]
            if w <= 0 or h <= 0:
                add("制造数据", "坐标偏移", "高",
                    f"图层 {g['name']} 图形范围异常 (W={w:.3f}, H={h:.3f})", layer=g["name"],
                    suggestion="检查坐标系/偏移与单位设定")
        for prim in g["primitives"]:
            if prim.get("type") == "error":
                add("制造数据", "孔径未定义", "高",
                    f"图层 {g['name']} 存在未定义光圈的 Flash", layer=g["name"],
                    x=prim.get("x1"), y=prim.get("y1"),
                    suggestion="修正光圈定义 (ADD)")
                break
        for dcode, ap in (g.get("apertures") or {}).items():
            params = ap.get("params") or []
            if params and isinstance(params[0], (int, float)) and params[0] <= 0:
                add("制造数据", "孔径定义错误", "高",
                    f"图层 {g['name']} 光圈 D{dcode} 尺寸<=0", layer=g["name"],
                    suggestion="修正光圈尺寸定义")

    for d in drills:
        if d.get("hole_count", 0) == 0:
            add("制造数据", "钻孔为空", "高", f"钻孔文件 {d['name']} 无孔", layer=d["name"],
                suggestion="检查钻孔输出")
        if d.get("plating") == "unknown":
            add("制造数据", "镀通孔标注", "低",
                f"钻孔文件 {d['name']} 未标注 PTH/NPTH", layer=d["name"],
                suggestion="按镀通孔/非镀通孔分文件输出")

    if len(gerbers) > 1:
        boxes = [(g["name"], g["bbox"]) for g in gerbers if g["bbox"][0] is not None]
        if boxes:
            ref = boxes[0][1]
            for name, b in boxes[1:]:
                if abs(b[0] - ref[0]) > 1.0 or abs(b[1] - ref[1]) > 1.0:
                    add("制造数据", "层间坐标偏移", "高",
                        f"图层 {name} 与 {boxes[0][0]} 坐标原点偏移 >1mm", layer=name,
                        x=b[0], y=b[1],
                        suggestion="统一各层输出坐标系/原点")

    return issues


# ============================================================
# 网络表比对
# ============================================================
def collect_fab_nets(fab_data: List[dict]) -> Dict[str, Set[str]]:
    nets: Dict[str, Set[str]] = {}
    for d in fab_data:
        for net, nodes in (d.get("nets") or {}).items():
            nets.setdefault(net, set()).update(nodes)
    return nets


def normalize_cad_nets(cad: Any) -> Dict[str, Set[str]]:
    nets: Dict[str, Set[str]] = {}
    if isinstance(cad, dict):
        for net, nodes in cad.items():
            if isinstance(nodes, (list, set, tuple)):
                nets[net] = {str(n).replace("-", ".") for n in nodes}
            else:
                nets[net] = set()
    elif isinstance(cad, list):
        for item in cad:
            if isinstance(item, dict) and "net" in item:
                nodes = item.get("nodes") or item.get("pins") or []
                nets[item["net"]] = {str(n).replace("-", ".") for n in nodes}
    return nets


def compare_netlists(cad_nets: Dict[str, Set[str]],
                     fab_nets: Dict[str, Set[str]]) -> dict:
    cad_nets = cad_nets or {}
    fab_nets = fab_nets or {}
    all_nets = sorted(set(cad_nets) | set(fab_nets))
    net_only_cad = sorted(set(cad_nets) - set(fab_nets))
    net_only_fab = sorted(set(fab_nets) - set(cad_nets))
    node_open: List[dict] = []
    node_short: List[dict] = []
    matched = 0
    for net in all_nets:
        c = cad_nets.get(net, set())
        f = fab_nets.get(net, set())
        if net in cad_nets and net in fab_nets:
            matched += 1
        for node in sorted(c - f):
            node_open.append({"net": net, "node": node,
                              "reason": "CAD 有网络连接但制造数据无此连接 (疑似开路 Open)"})
        for node in sorted(f - c):
            node_short.append({"net": net, "node": node,
                               "reason": "制造数据有额外网络连接 (疑似短路 Short)"})

    passed = not (net_only_cad or net_only_fab or node_open or node_short)
    return {
        "cad_net_count": len(cad_nets),
        "fab_net_count": len(fab_nets),
        "matched_nets": matched,
        "net_only_cad": net_only_cad,
        "net_only_fab": net_only_fab,
        "node_open": node_open,
        "node_short": node_short,
        "open_count": len(node_open) + len(net_only_cad),
        "short_count": len(node_short) + len(net_only_fab),
        "passed": passed,
    }


def netlist_compare_issues(comparison: dict) -> List[dict]:
    issues: List[dict] = []
    counter = 0

    def add(rule, severity, desc, measured=None, required=None, unit="", suggestion=""):
        nonlocal counter
        counter += 1
        issues.append(make_issue(
            code=f"NET-{counter:02d}", category="网络比对", rule=rule,
            severity=severity, description=desc, measured=measured,
            required=required, unit=unit, suggestion=suggestion,
        ))

    for item in comparison.get("node_open", []):
        add("开路(Open)", "危险",
            f"网络 {item['net']} 节点 {item['node']} 在制造数据中缺失连接",
            item["node"], "应连接", "", "核对网络/铺铜连接, 修正开路")
    for item in comparison.get("node_short", []):
        add("短路(Short)", "危险",
            f"网络 {item['net']} 节点 {item['node']} 在制造数据中出现额外连接",
            item["node"], "无连接", "", "检查孤立铜/残铜, 修正短路")
    for net in comparison.get("net_only_cad", []):
        add("网络缺失", "高", f"CAD 网络 {net} 在制造数据中不存在",
            net, "需存在", "", "核对网络是否漏输出/被合并")
    for net in comparison.get("net_only_fab", []):
        add("多余网络", "高", f"制造数据出现 CAD 中不存在的网络 {net}",
            net, "不应存在", "", "核对多余网络来源")
    if not issues:
        issues.append(make_issue(code="NET-00", category="网络比对", rule="网络一致性",
                                 severity="低", description="网络表比对通过, 未发现开路/短路",
                                 suggestion="无需处理"))
    return issues


def analyze_fab(path: str) -> dict:
    data = read_fab_data(path)
    issues = integrity_check(data)
    stats = {
        "gerber_layers": [d["name"] for d in data if d["kind"] == "gerber"],
        "drill_files": [d["name"] for d in data if d["kind"] == "drill"],
        "odb_jobs": [d["name"] for d in data if d["kind"] == "odbpp"],
        "feature_count": sum(d.get("feature_count", 0) for d in data),
        "hole_count": sum(d.get("hole_count", 0) for d in data),
        "min_line_width_mm": _min_attr(data, "min_line_width_mm"),
        "min_hole_mm": _min_attr(data, "min_hole_mm"),
        "nets": collect_fab_nets(data),
    }
    return {"data": data, "issues": issues, "stats": stats}


def _min_attr(data, key):
    vals = [d.get(key) for d in data if d.get(key) is not None]
    return round(min(vals), 4) if vals else None


def build_fab_report(fab_result: dict, comparison: Optional[dict] = None,
                     path: str = "") -> dict:
    """将制造数据 DFM 结果整理为与 dfm_report.generate_dfm_excel 兼容的结构"""
    from src.dfm_rules import summarize_issues, compute_risk_score
    issues = list(fab_result.get("issues", []))
    if comparison is not None:
        issues += netlist_compare_issues(comparison)
    stats = fab_result.get("stats", {})
    info = {
        "manufacturing": {
            "Gerber图层数": len(stats.get("gerber_layers", [])),
            "钻孔文件数": len(stats.get("drill_files", [])),
            "ODB++工程数": len(stats.get("odb_jobs", [])),
            "图形总数": stats.get("feature_count"),
            "孔总数": stats.get("hole_count"),
            "最小线宽": stats.get("min_line_width_mm"),
            "最小孔径": stats.get("min_hole_mm"),
        },
        "assembly": {},
        "capability_notes": {},
    }
    return {
        "factory_label": "制造数据",
        "board_type_label": os.path.basename(os.path.normpath(path)) if path else "Fabrication",
        "capability_label": "Gerber/ODB++/NC Drill",
        "copper_um": None,
        "issues": issues,
        "issue_summary": summarize_issues(issues),
        "risk": compute_risk_score(issues),
        "info_stats": info,
    }