"""ODB++ 解析 (matrix / features / netlist / components / 压缩包)。

相对旧实现的修复:
  * **符号表真正生效**: ``$0 r0.5`` / ``$3 r[REDACTED-UNKNOWN].5`` / ``$5 o0.[REDACTED-UNKNOWN].4`` 等解析为
    真实形状与尺寸, flash/line 携带 ``shape/w/h/rot`` → 线宽、孔环、阻焊桥不再失真
  * **铺铜 surface**: ``S`` + ``OB/OE``(+``OS`` 内孔) 轮廓解析为多边形 (旧实现完全丢失)
  * **阵列 ``R``**: 按 ODB++ 语义重复前一个 feature 块
  * **极性**: 记录级 polarity 与 matrix 的 ``POLARITY=NEGATIVE`` → 统一转成
    ``clear`` 标记, 交给 :mod:`dfm.geom.polarity` 合成
  * **matrix**: 正确解析 ``STEP{}`` / ``LAYER{}`` 块 (层名/类型/极性/顺序)
  * **压缩与打包**: ``features.z``(gzip)、``job.tgz``/``job.tar``/``job.zip`` 自动解包
  * **components**: 器件位号/封装/坐标/面 → 供装配检查用真实器件信息
  * **profile**: 板框轮廓层
"""
from __future__ import annotations

import gzip
import os
import re
import shutil
import tarfile
import tempfile
import zipfile
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

INCH_TO_MM = 25.4

#: matrix LAYER.TYPE → 内部分类 (与 dfm.io.gerber_model 的 KIND_* 对齐)
TYPE_MAP = {
    "SIGNAL": "copper", "POWER_GROUND": "copper", "MIXED": "copper",
    "SOLDER_MASK": "soldermask", "MASK": "soldermask",
    "SILK_SCREEN": "silkscreen", "PASTE_MASK": "paste", "SOLDER_PASTE": "paste",
    "DRILL": "drill", "ROUT": "profile", "PROFILE": "profile",
    "DOCUMENT": "other", "COMPONENT": "other",
}

_SYM_NUM_RE = re.compile(r"^([a-zA-Z]+)(.*)$")


# ============================================================
# 符号表
# ============================================================
def parse_symbol(token: str) -> dict:
    """解析 ODB++ 标准符号名 → {shape, w, h, rot, vertices}。

    支持: ``r<d>`` 圆 / ``r<w>X<h>`` 矩形 / ``s<w>`` 正方 / ``e<w>X<h>`` 椭圆 /
    ``o<w>X<h>`` 椭圆(oblong) / ``d<w>X<h>`` 菱形 / ``t<..>`` 梯形 /
    ``p<n>X<d>X<ang>`` 正多边形 / ``rect<w>X<h>`` / ``oval<w>X<h>``
    """
    token = (token or "").strip()
    if not token:
        return {"shape": "circle", "w": 0.0, "h": 0.0, "rot": 0.0, "raw": token}
    m = _SYM_NUM_RE.match(token)
    head = (m.group(1) if m else token).lower()
    tail = m.group(2) if m else ""
    nums: List[float] = []
    for p in re.split(r"[Xx,]", tail):
        p = p.strip()
        if not p:
            continue
        try:
            nums.append(float(p))
        except ValueError:
            continue

    def n(i: int, default: float = 0.0) -> float:
        return nums[i] if i < len(nums) else default

    if head in ("r", "round", "circle"):
        if len(nums) >= 2:
            return {"shape": "rect", "w": n(0), "h": n(1), "rot": n(2), "raw": token}
        return {"shape": "circle", "w": n(0), "h": n(0), "rot": 0.0, "raw": token}
    if head in ("s", "square"):
        return {"shape": "rect", "w": n(0), "h": n(0), "rot": 0.0, "raw": token}
    if head in ("rect", "rectangle"):
        return {"shape": "rect", "w": n(0), "h": n(1), "rot": n(2), "raw": token}
    if head in ("o", "oval", "oblong"):
        return {"shape": "oval", "w": n(0), "h": n(1), "rot": n(2), "raw": token}
    if head in ("e", "ellipse"):
        return {"shape": "oval", "w": n(0), "h": n(1), "rot": n(2), "raw": token}
    if head in ("d", "diamond"):
        return {"shape": "diamond", "w": n(0), "h": n(1), "rot": 0.0, "raw": token}
    if head in ("t", "trapezoid"):
        return {"shape": "trapezoid", "w": n(0), "h": n(2), "rot": 0.0, "raw": token}
    if head in ("p", "poly", "polygon"):
        return {"shape": "poly", "w": n(1), "h": n(1), "rot": n(2),
                "vertices": int(n(0)) or 6, "raw": token}
    if head in ("donut",):
        return {"shape": "circle", "w": n(0), "h": n(0), "rot": 0.0,
                "hole": n(1), "raw": token}
    # 未识别符号: 取第一个数值作为直径 (保守), 并在 notes 中提示
    return {"shape": "circle", "w": n(0), "h": n(0), "rot": 0.0,
            "raw": token, "unknown": True}


# ============================================================
# 文件读取 (支持 gzip)
# ============================================================
def read_text(path: str) -> Optional[str]:
    """读取文本; ``*.z``/``*.Z`` 视为 gzip 解压。"""
    if not os.path.isfile(path):
        gz = path + ".z"
        if os.path.isfile(gz):
            path = gz
        elif os.path.isfile(path + ".Z"):
            path = path + ".Z"
        else:
            return None
    try:
        if path.lower().endswith((".z", ".gz")):
            with gzip.open(path, "rt", encoding="utf-8", errors="ignore") as f:
                return f.read()
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return None


def extract_archive(path: str, workdir: Optional[str] = None) -> Optional[str]:
    """解包 ODB++ 作业压缩包 (tgz/tar/zip), 返回解包后的作业根目录。"""
    low = path.lower()
    if not low.endswith((".tgz", ".tar", ".tar.gz", ".zip", ".gz")):
        return None
    dest = workdir or tempfile.mkdtemp(prefix="evan_odb_")
    os.makedirs(dest, exist_ok=True)
    try:
        if low.endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                z.extractall(dest)
        elif low.endswith((".tgz", ".tar.gz", ".tar")):
            with tarfile.open(path, "r:*") as t:
                t.extractall(dest, filter="data")
        elif low.endswith(".gz"):
            out = os.path.join(dest, os.path.basename(path)[:-3])
            with gzip.open(path, "rb") as fsrc, open(out, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst)
        else:
            return None
    except Exception:
        return None
    return _find_job_root(dest)


def _find_job_root(root: str) -> str:
    """定位含 matrix/matrix 的作业根目录。"""
    if os.path.isfile(os.path.join(root, "matrix", "matrix")):
        return root
    for dirpath, dirnames, _files in os.walk(root):
        if "matrix" in dirnames and os.path.isfile(
                os.path.join(dirpath, "matrix", "matrix")):
            return dirpath
    return root


# ============================================================
# matrix
# ============================================================
def parse_matrix(text: str) -> dict:
    """解析 matrix/matrix: units / steps / layers。"""
    out: Dict[str, Any] = {"units": "inch", "steps": [], "layers": [], "version": ""}
    if not text:
        return out
    lines = [ln.strip() for ln in text.splitlines()]
    i = 0
    cur: Optional[Dict[str, str]] = None
    kind = ""
    while i < len(lines):
        line = lines[i]
        up = line.upper()
        if up.startswith("UNITS"):
            val = line.split("=")[-1].strip().upper()
            out["units"] = "mm" if val.startswith("MM") else "inch"
        elif up.startswith("VERSION"):
            out["version"] = line.split("=")[-1].strip()
        elif up.startswith("STEP") and "{" in up:
            kind, cur = "step", {}
        elif up.startswith("LAYER") and "{" in up:
            kind, cur = "layer", {}
        elif up.startswith("}") and cur is not None:
            if kind == "step":
                out["steps"].append(cur)
            elif kind == "layer":
                out["layers"].append(cur)
            cur, kind = None, ""
        elif cur is not None and "=" in line:
            k, _, v = line.partition("=")
            cur[k.strip().upper()] = v.strip().strip(";")
        i += 1
    return out


# ============================================================
# features
# ============================================================
def parse_features(text: str, layer_name: str, scale: float,
                   negative_layer: bool = False) -> Tuple[List[dict], List[str], Dict[int, dict]]:
    """解析一个 features 文件 → (图元列表, 提示列表, 符号表)。"""
    feats: List[dict] = []
    notes: List[str] = []
    symbols: Dict[int, dict] = {}
    block: List[dict] = []          # 供 R 阵列重复的"上一个块"
    surface: Optional[dict] = None
    surface_pts: List[Tuple[float, float]] = []
    surface_holes: List[List[Tuple[float, float]]] = []
    outline_target = ""             # "" | "outer" | "hole"
    r_count = 0
    unknown_syms: Set[str] = set()

    def num(tok: str) -> float:
        try:
            return float(tok) * scale
        except (TypeError, ValueError):
            return 0.0

    def attrs(text_: str) -> Dict[str, str]:
        """解析记录属性段。

        ODB++ 属性写在 ';' 之后, 以**逗号**分隔: ``;.net_name=GND,.comp=R1,.pn=1``;
        部分工具也用 ';' 分隔, 两种都支持。键名去掉前导 '.'。
        """
        out: Dict[str, str] = {}
        if ";" not in text_:
            return out
        attr_part = text_.split(";", 1)[1]
        for part in re.split(r"[,;]", attr_part):
            part = part.strip()
            if not part or "=" not in part:
                continue
            k, _, v = part.partition("=")
            out[k.strip().lstrip(".").lower()] = v.strip()
        return out

    def prim(d: dict) -> None:
        feats.append(d)
        block.append(d)

    def flush_block() -> None:
        block.clear()

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("$"):
            parts = line[1:].split(None, 1)
            if len(parts) >= 2:
                try:
                    idx = int(parts[0])
                    symbols[idx] = parse_symbol(parts[1].strip())
                except ValueError:
                    notes.append(f"无法解析的符号定义: {line[:40]}")
            continue
        if line.startswith("#"):
            continue

        # ---- surface (铺铜) ----
        up_head = line.split()[0].upper()
        if up_head == "S":
            surface = {"polarity": _polarity(line, 1, negative_layer),
                       "attrs": attrs(line)}
            surface_pts, surface_holes = [], []
            outline_target = ""
            continue
        if up_head == "OB":
            outline_target = "outer"
            surface_holes = []
            parts = line.split()
            if len(parts) >= 3:
                surface_pts.append((num(parts[1]), num(parts[2])))
            continue
        if up_head in ("OS", "OI"):
            outline_target = "hole"
            surface_holes.append([])
            parts = line.split()
            if len(parts) >= 3:
                surface_holes[-1].append((num(parts[1]), num(parts[2])))
            continue
        if up_head == "OE":
            outline_target = ""
            if surface is not None and len(surface_pts) >= 3:
                xs = [p[0] for p in surface_pts]
                ys = [p[1] for p in surface_pts]
                prim({
                    "type": "region", "points": list(surface_pts),
                    "holes": [list(h) for h in surface_holes if len(h) >= 3],
                    "x1": min(xs), "y1": min(ys), "x2": max(xs), "y2": max(ys),
                    "layer": layer_name, "clear": surface["polarity"] == "clear",
                    "net": surface["attrs"].get("net_name", "") or surface["attrs"].get("net", ""),
                    "refdes": surface["attrs"].get("comp", ""),
                    "pin": surface["attrs"].get("pin", ""),
                    "width": None,
                })
            surface = None
            surface_pts, surface_holes = [], []
            continue
        if outline_target:
            parts = line.split()
            if len(parts) >= 2:
                try:
                    pt = (float(parts[0]) * scale, float(parts[1]) * scale)
                except ValueError:
                    continue
                if outline_target == "hole" and surface_holes:
                    surface_holes[-1].append(pt)
                else:
                    surface_pts.append(pt)
            continue

        # ---- 阵列 R ----
        if up_head == "R":
            parts = line.split()
            try:
                x, y = num(parts[1]), num(parts[2])
                dx, dy = num(parts[3]), num(parts[4])
                nx, ny = int(float(parts[5])), int(float(parts[6]))
            except (IndexError, ValueError):
                notes.append(f"无法解析的阵列记录: {line[:48]}")
                continue
            r_count += 1
            copies: List[dict] = []
            for ix in range(nx):
                for iy in range(ny):
                    if ix == 0 and iy == 0:
                        continue
                    ox, oy = x + dx * ix, y + dy * iy
                    for src in block:
                        c = dict(src)
                        for key in ("x1", "x2"):
                            if isinstance(c.get(key), (int, float)):
                                c[key] = c[key] + ox
                        for key in ("y1", "y2"):
                            if isinstance(c.get(key), (int, float)):
                                c[key] = c[key] + oy
                        if c.get("points"):
                            c["points"] = [(px + ox, py + oy) for px, py in c["points"]]
                        copies.append(c)
            feats.extend(copies)
            continue

        parts = line.split()
        head = parts[0].upper()
        body_attrs = attrs(line)
        # 去掉 ';' 之后的属性部分再取字段
        core = line.split(";", 1)[0].split()

        if head == "L" and len(core) >= 7:
            sym = _sym(core, 5, symbols, unknown_syms, notes)
            prim({
                "type": "draw", "x1": num(core[1]), "y1": num(core[2]),
                "x2": num(core[3]), "y2": num(core[4]),
                "width": sym["w"], "shape": sym["shape"], "w": sym["w"], "h": sym["h"],
                "rot": sym.get("rot", 0.0),
                "layer": layer_name, "clear": _pol(core, 6, negative_layer),
                "net": body_attrs.get("net_name", "") or body_attrs.get("net", ""),
                "refdes": (body_attrs.get("comp") or body_attrs.get("cmp") or ""),
                "pin": (body_attrs.get("pn") or body_attrs.get("pin") or ""),
                "sym": sym.get("raw", ""),
            })
        elif head == "P" and len(core) >= 5:
            sym = _sym(core, 3, symbols, unknown_syms, notes)
            prim({
                "type": "flash", "x1": num(core[1]), "y1": num(core[2]),
                "x2": num(core[1]), "y2": num(core[2]),
                "width": (min(sym["w"], sym["h"]) if min(sym["w"], sym["h"]) > 0
                          else max(sym["w"], sym["h"])),
                "shape": sym["shape"], "w": sym["w"], "h": sym["h"],
                "rot": sym.get("rot", 0.0), "hole": sym.get("hole"),
                "layer": layer_name, "clear": _pol(core, 4, negative_layer),
                "net": body_attrs.get("net_name", "") or body_attrs.get("net", ""),
                "refdes": (body_attrs.get("comp") or body_attrs.get("cmp") or ""),
                "pin": (body_attrs.get("pn") or body_attrs.get("pin") or ""),
                "feat_type": body_attrs.get("type", ""),
                "sym": sym.get("raw", ""),
            })
        elif head == "A" and len(core) >= 9:
            sym = _sym(core, 7, symbols, unknown_syms, notes)
            cw = False
            if len(core) > 9:
                cw = str(core[9]).strip() in ("0", "CW", "cw")
            prim({
                "type": "arc", "x1": num(core[1]), "y1": num(core[2]),
                "cx": num(core[3]), "cy": num(core[4]),
                "x2": num(core[5]), "y2": num(core[6]),
                "cw": cw,
                "width": sym["w"], "shape": sym["shape"], "w": sym["w"], "h": sym["h"],
                "i": num(core[3]) - num(core[1]), "j": num(core[4]) - num(core[2]),
                "layer": layer_name, "clear": _pol(core, 8, negative_layer),
                "net": body_attrs.get("net_name", "") or body_attrs.get("net", ""),
                "refdes": (body_attrs.get("comp") or body_attrs.get("cmp") or ""),
                "pin": (body_attrs.get("pn") or body_attrs.get("pin") or ""),
                "sym": sym.get("raw", ""),
            })
        elif head in ("BD", "T", "SE", "PSE"):
            continue
        elif len(head) > 1 and head not in ("L", "P", "A", "S", "R", "OB", "OE", "OS", "OI"):
            notes.append(f"忽略未支持的记录类型: {head}")

    if unknown_syms:
        notes.append(f"{len(unknown_syms)} 个符号未定义, 按 0 尺寸处理: "
                     f"{sorted(unknown_syms)[:5]}")
    if r_count:
        notes.append(f"解析到 {r_count} 条阵列(R)记录, 已展开为实例")
    return feats, notes, symbols


def _sym(core: Sequence[str], idx: int, symbols: Dict[int, dict],
         unknown: Set[str], notes: List[str]) -> dict:
    try:
        num_key = int(core[idx])
    except (IndexError, ValueError):
        return {"shape": "circle", "w": 0.0, "h": 0.0, "rot": 0.0, "raw": ""}
    sym = symbols.get(num_key)
    if sym is None:
        unknown.add(str(num_key))
        return {"shape": "circle", "w": 0.0, "h": 0.0, "rot": 0.0, "raw": f"${num_key}"}
    return sym


def _pol(core: Sequence[str], idx: int, negative_layer: bool) -> bool:
    """ODB++ 极性字段: 0=positive(dark), 1=negative(clear); 层负片时整体反转。"""
    try:
        val = str(core[idx]).strip()
    except IndexError:
        val = "0"
    is_clear = val.startswith("1") or val.upper().startswith("N")
    return is_clear != bool(negative_layer)


def _polarity(line: str, idx: int, negative_layer: bool) -> str:
    parts = line.split(";")[0].split()
    return "clear" if _pol(parts, idx, negative_layer) else "dark"


# ============================================================
# netlist / components
# ============================================================
def parse_netlist(text: str) -> Dict[str, Set[str]]:
    """解析 ODB++ CAD 网表 (``netlists/cadnet/netlist``)。

    兼容常见写法::

        GND                 NET_GND
        +R1.1               +R1.1;R1.2
        +R1.2               NET_GND;R1.1,R1.2

    规则: 不以 ``+`` 开头的行是网络名 (其后 ``;``/``,`` 分隔的部分也算节点);
    以 ``+`` 开头的行是当前网络的节点。
    """
    nets: Dict[str, Set[str]] = {}
    cur: Optional[str] = None
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";", "//")):
            continue
        is_node = line.startswith("+")
        body = line[1:].strip() if is_node else line
        tokens = [t.strip() for t in re.split(r"[;,]", body) if t.strip()]
        if not tokens:
            continue
        if is_node:
            if cur:
                nets[cur].update(tokens)
            continue
        cur = tokens[0]
        nets.setdefault(cur, set())
        if len(tokens) > 1:
            nets[cur].update(tokens[1:])
    return {k: v for k, v in nets.items() if k}


def parse_components(text: str, scale: float) -> List[dict]:
    """解析 ``steps/<step>/components`` → 器件列表 (位号/封装/坐标/面)。

    ODB++ 该文件存在多种写法, 故不按固定列号取值, 而是**定位首个连续的三个数值**
    作为 (x, y, rotation), 其前两个字段即封装名与位号::

        CMP 0 R1 RESC1005 12 10 0 TOP        (带记录名与序号)
        TOP R1 R0402 1.5 2.5 90              (以面开头)
        R1 R0402 1.5 2.5 90 bottom           (无记录名)
    """
    out: List[dict] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";", "//")):
            continue
        parts = line.replace(";", " ").split()
        if len(parts) < 5:
            continue
        head = parts[0].upper()
        if head not in ("CMP", "COMP", "TOP", "BOT", "BOTTOM", "SMD", "TH"):
            continue

        nums = [_as_float(t) for t in parts]
        start = None
        for i in range(len(nums) - 2):
            if nums[i] is not None and nums[i + 1] is not None:
                # x/y 必须是数值; rotation 可缺省
                start = i
                break
        if start is None:
            continue
        x = nums[start] * scale
        y = nums[start + 1] * scale
        rot = (nums[start + 2] or 0.0) if start + 2 < len(nums) else 0.0
        pkg = parts[start - 1] if start >= 1 else ""
        refdes = parts[start - 2] if start >= 2 else pkg
        if not refdes or _as_float(refdes) is not None:
            # 前一位是序号(如 "CMP 0 R1 ..."), 再往前取
            refdes = pkg
            pkg = parts[start - 2] if start >= 2 else ""
        side = ""
        for tok in parts[start + 2:]:
            up = tok.strip().upper().rstrip(";")
            if up in ("TOP", "T"):
                side = "top"
                break
            if up in ("BOTTOM", "BOT", "B"):
                side = "bottom"
                break
        if not side:
            side = "bottom" if head in ("BOT", "BOTTOM") else "top"
        out.append({
            "refdes": refdes,
            "package": pkg,
            "x": x, "y": y, "rotation": rot,
            "side": side,
            "pins": None,
        })
    return out


def _as_float(tok: Any) -> Optional[float]:
    try:
        return float(str(tok).strip())
    except (TypeError, ValueError):
        return None


def _grow_bbox(bbox: List[Optional[float]], x, y, half: float = 0.0) -> None:
    """按点位与外扩半径 (半个符号尺寸) 扩展包围盒。

    必须外扩: 只按坐标点计算会让"仅含焊盘/横向走线"的层得到 H=0 的退化包围盒,
    进而被完整性检查误判为坐标偏移。
    """
    if x is None or y is None:
        return
    x, y = float(x), float(y)
    bbox[0] = x - half if bbox[0] is None else min(bbox[0], x - half)
    bbox[1] = y - half if bbox[1] is None else min(bbox[1], y - half)
    bbox[2] = x + half if bbox[2] is None else max(bbox[2], x + half)
    bbox[3] = y + half if bbox[3] is None else max(bbox[3], y + half)


# ============================================================
# 作业级解析
# ============================================================
def parse_job(job_dir: str) -> dict:
    """解析一个 ODB++ 作业目录, 返回与 Gerber 解析一致的多层数据结构。

    返回::

        {"kind": "odbpp", "name": ..., "unit": "mm"|"inch",
         "layers": [ {kind,name,file,features,bbox,nets,...}, ... ],
         "nets": {net: {refdes.pin}}, "components": [...],
         "steps": [...], "notes": [...], "matrix": {...}}
    """
    notes: List[str] = []
    matrix_text = read_text(os.path.join(job_dir, "matrix", "matrix"))
    matrix = parse_matrix(matrix_text or "")
    if not matrix_text:
        notes.append("未找到 matrix/matrix, 层类型将由文件名推断")
    unit = matrix.get("units") or "inch"
    scale = INCH_TO_MM if unit == "inch" else 1.0

    steps = matrix.get("steps") or []
    step_name = steps[0].get("NAME") if steps else None
    if not step_name:
        steps_dir = os.path.join(job_dir, "steps")
        if os.path.isdir(steps_dir):
            cand = [d for d in sorted(os.listdir(steps_dir))
                    if os.path.isdir(os.path.join(steps_dir, d))]
            step_name = cand[0] if cand else None
    if not step_name:
        notes.append("未找到 step, 无法定位 features")

    layer_rows = matrix.get("layers") or []
    layers_out: List[dict] = []
    all_nets: Dict[str, Set[str]] = {}
    components: List[dict] = []

    for idx, row in enumerate(layer_rows, start=1):
        lname = row.get("NAME") or f"layer{idx}"
        ltype = (row.get("TYPE") or "").upper()
        kind = TYPE_MAP.get(ltype, "other")
        neg = (row.get("POLARITY") or "").upper().startswith("NEG")
        feat_path = None
        if step_name:
            base = os.path.join(job_dir, "steps", step_name, "layers", lname)
            for cand in ("features", "features.z", "features.Z"):
                p = os.path.join(base, cand)
                if os.path.isfile(p):
                    feat_path = p
                    break
        if feat_path is None:
            alt = os.path.join(job_dir, "layers", lname, "features")
            if os.path.isfile(alt) or os.path.isfile(alt + ".z"):
                feat_path = alt
        if feat_path is None:
            notes.append(f"层 {lname} 无 features 文件, 已跳过")
            continue
        text = read_text(feat_path)
        if text is None:
            if os.path.isfile(feat_path) and feat_path.lower().endswith(".z"):
                notes.append(f"层 {lname} 为 Unix compress(.Z) 格式, Python 无法解压; "
                             f"请先解包 (gzip -d 或 ODB++ 工具导出未压缩作业)")
            continue
        feats, fn, _syms = parse_features(text, lname, scale, negative_layer=neg)
        for n in fn:
            if n not in notes:
                notes.append(n)
        bbox: List[Optional[float]] = [None, None, None, None]
        nets: Dict[str, Set[str]] = {}

        for f in feats:
            half = max(float(f.get("w") or 0.0), float(f.get("h") or 0.0),
                       float(f.get("width") or 0.0)) / 2.0
            for key in (("x1", "y1"), ("x2", "y2")):
                _grow_bbox(bbox, f.get(key[0]), f.get(key[1]), half)
            for pt in f.get("points") or []:
                _grow_bbox(bbox, pt[0], pt[1])
            net = f.get("net")
            if net and f.get("refdes") and f.get("pin"):
                nets.setdefault(net, set()).add(f"{f['refdes']}.{f['pin']}")
        all_nets.update({k: all_nets.get(k, set()) | v for k, v in nets.items()})
        widths = [f.get("width") for f in feats
                  if f.get("type") in ("draw", "arc") and f.get("width")]
        pads = [min(f.get("w") or 0, f.get("h") or 0) or max(f.get("w") or 0, f.get("h") or 0)
                for f in feats if f.get("type") == "flash"]
        layers_out.append({
            "kind": "odbpp",
            "layer_kind": kind,
            "odb_type": ltype,
            "name": lname,
            "file": feat_path,
            "unit": unit,
            "negative_layer": neg,
            "features": feats,
            "feature_count": len(feats),
            "bbox": bbox,
            "nets": {k: sorted(v) for k, v in nets.items()},
            "attributes_present": bool(nets),
            "min_line_width_mm": round(min(widths), 4) if widths else None,
            "min_pad_mm": round(min(pads), 4) if pads else None,
            "file_function": f"{ltype.title()},L{idx}" if kind == "copper" else ltype.title(),
        })

    # 钻孔层单独输出为 drill 结构 (与 Excellon 解析一致)
    drills: List[dict] = []
    for layer in list(layers_out):
        if layer.get("layer_kind") != "drill":
            continue
        holes = []
        for f in layer.get("features") or []:
            if f.get("type") != "flash":
                continue
            d = max(f.get("w") or 0.0, f.get("h") or 0.0)
            holes.append({"x": f.get("x1"), "y": f.get("y1"), "diameter_mm": d,
                          "plating": "PTH", "tool": None, "is_slot": False})
        drills.append({
            "kind": "drill", "name": layer["name"], "file": layer["file"],
            "unit": unit, "plating": "unknown", "holes": holes,
            "hole_count": len(holes), "bbox": layer["bbox"],
            "notes": [f"由 ODB++ 层 {layer['name']} 转换"],
        })

    # CAD 网表
    if step_name:
        for rel in (("netlists", "cadnet", "netlist"), ("netlists", "cadnet", "netlist.z")):
            text = read_text(os.path.join(job_dir, "steps", step_name, *rel))
            if text:
                parsed = parse_netlist(text)
                for k, v in parsed.items():
                    all_nets.setdefault(k, set()).update(v)
                break
        comp_text = read_text(os.path.join(job_dir, "steps", step_name, "components")) \
            or read_text(os.path.join(job_dir, "steps", step_name, "components.z"))
        if comp_text:
            components = parse_components(comp_text, scale)

    return {
        "kind": "odbpp_job",
        "name": os.path.basename(os.path.normpath(job_dir)),
        "file": job_dir,
        "unit": unit,
        "layers": layers_out,
        "drills": drills,
        "nets": {k: sorted(v) for k, v in all_nets.items()},
        "components": components,
        "steps": steps,
        "matrix": matrix,
        "notes": notes,
        "feature_count": sum(l.get("feature_count", 0) for l in layers_out),
        "hole_count": sum(d.get("hole_count", 0) for d in drills),
    }
