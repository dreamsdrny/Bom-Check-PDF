"""Excellon NC 钻孔解析 (含刀具表、重复孔、槽孔)。

相对旧实现的修复:
  * **槽孔**: ``G85`` 直线槽 与 ``M15/M16`` + ``G01/G02/G03`` 铣槽路径 →
    输出 ``is_slot/x2/y2/length_mm``, 使孔到线、孔到板边、孔环检查不再漏判
  * **重复孔 ``R##``**: 绝对模式下按次数展开; 增量模式 (G91) 下按偏移步进
  * **刀具表**: 支持 ``T01C0.300F200H500`` 形式, 以及正文中临时改刀具
  * **零省略模式**: ``METRIC,LZ`` / ``METRIC,TZ`` / ``INCH,LZ`` 与 ``;FILE_FORMAT=i:d``
  * ``T00`` 卸刀、``G90/G91`` 绝对/增量、``G05`` 钻孔模式、``M00/M30`` 结束
  * 镀层属性: ``;TYPE=PLATED/NON_PLATED`` 注释 > 文件名推断
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

INCH_TO_MM = 25.4
_NUM = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"

_TOOL_DEF_RE = re.compile(r"^T(\d+)\s*C(" + _NUM + r")(.*)$")
_TOOL_SEL_RE = re.compile(r"^T(\d+)\s*$")
_HIT_RE = re.compile(r"^(X(" + _NUM + r"))?(Y(" + _NUM + r"))?(R(\d+))?$")
_SLOT_RE = re.compile(r"^X(" + _NUM + r")Y(" + _NUM + r")G85X(" + _NUM + r")Y(" + _NUM + r")")
_FMT_RE = re.compile(r"FILE_FORMAT\s*=\s*(\d):(\d)")


class ExcellonData:
    def __init__(self, name: str = ""):
        self.name = name
        self.unit = "inch"
        self.dec_digits: Optional[int] = None
        self.int_digits: Optional[int] = None
        self.trailing_zeros_kept = False     # TZ: 尾随零保留 → 左对齐
        self.incremental = False             # G91
        self.tools: Dict[int, dict] = {}
        self.holes: List[dict] = []
        self.notes: List[str] = []
        self.plating_default = "unknown"
        self.slot_count = 0
        self.repeat_count = 0
        self.rout_paths = 0
        self.bbox: List[Optional[float]] = [None, None, None, None]

    def add_point(self, x, y) -> None:
        if x is None or y is None:
            return
        b = self.bbox
        b[0] = x if b[0] is None else min(b[0], x)
        b[1] = y if b[1] is None else min(b[1], y)
        b[2] = x if b[2] is None else max(b[2], x)
        b[3] = y if b[3] is None else max(b[3], y)


def _detect_plating(path: str, comments: List[str]) -> str:
    for c in comments:
        up = c.upper()
        if "NON_PLATED" in up or "NONPLATED" in up or "NPTH" in up:
            return "NPTH"
        if "PLATED" in up or "PTH" in up:
            return "PTH"
    name = os.path.basename(path).upper()
    if "NPTH" in name or "NON" in name or "NPT" in name:
        return "NPTH"
    if "PTH" in name or "DRL" in name or "PLATED" in name:
        return "PTH"
    return "unknown"


def parse(path: str, plating: Optional[str] = None) -> dict:
    """解析 Excellon 钻孔文件。"""
    with open(path, encoding="utf-8", errors="ignore") as f:
        raw_lines = f.read().splitlines()

    res = ExcellonData(os.path.basename(path))
    comments = [ln.strip() for ln in raw_lines if ln.strip().startswith(";")]

    # 数字格式: 优先注释 ;FILE_FORMAT=i:d
    for c in comments:
        m = _FMT_RE.search(c.replace(" ", ""))
        if m:
            res.int_digits = int(m.group(1))
            res.dec_digits = int(m.group(2))
            break

    in_header = False
    cur_tool: Optional[int] = None
    cur_plating = plating or _detect_plating(path, comments)
    res.plating_default = cur_plating
    cur_x = cur_y = 0.0
    in_rout = False                        # M15 按下 → 铣槽中
    rout_start: Optional[tuple] = None
    pending_g = 1

    def scale() -> float:
        return INCH_TO_MM if res.unit == "inch" else 1.0

    def dec() -> int:
        if res.dec_digits is not None:
            return res.dec_digits
        return 3 if res.unit == "mm" else 4

    def coord(raw: Optional[str]) -> Optional[float]:
        if raw is None:
            return None
        raw = raw.strip()
        if not raw:
            return None
        if "." in raw:
            try:
                return float(raw) * scale()
            except ValueError:
                return None
        neg = raw.startswith("-")
        digits = raw.lstrip("+-")
        if not digits.isdigit():
            return None
        d = dec()
        if res.trailing_zeros_kept:        # 尾随零保留 → 需右侧补零到总位宽
            total = (res.int_digits or 3) + d
            digits = digits.ljust(total, "0")
        v = int(digits) / (10.0 ** d)
        if neg:
            v = -v
        return v * scale()                 # 英寸 → mm (旧实现漏了这步)

    def tool_diameter(t: Optional[int]) -> Optional[float]:
        if t is None:
            return None
        info = res.tools.get(t) or {}
        return info.get("diameter_mm")

    def add_hole(x, y, tool, is_slot=False, x2=None, y2=None, plating_extra=None) -> None:
        d = tool_diameter(tool)
        h = {
            "x": x, "y": y, "tool": tool, "diameter_mm": d,
            "plating": plating_extra or cur_plating,
            "is_slot": bool(is_slot),
        }
        if is_slot and x2 is not None:
            h["x2"] = x2
            h["y2"] = y2
            h["length_mm"] = round(((x2 - x) ** 2 + (y2 - y) ** 2) ** 0.5, 4)
            res.slot_count += 1
        res.holes.append(h)
        res.add_point(x, y)
        if is_slot and x2 is not None:
            res.add_point(x2, y2)
        else:
            r = (d or 0.0) / 2.0
            res.add_point((x or 0) - r, (y or 0) - r)
            res.add_point((x or 0) + r, (y or 0) + r)

    for raw in raw_lines:
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        up = line.upper()

        if up == "M48":
            in_header = True
            continue
        if up in ("%", "M95"):
            in_header = False
            continue
        if up.startswith("METRIC") or up.startswith("M71"):
            res.unit = "mm"
            if up.startswith("METRIC"):
                res.trailing_zeros_kept = "TZ" in up
            continue
        if up.startswith("INCH") or up.startswith("M72"):
            res.unit = "inch"
            res.trailing_zeros_kept = "TZ" in up
            continue
        if up.startswith("FMAT"):
            continue
        if up.startswith("VER"):
            continue
        if up in ("G90",):
            res.incremental = False
            continue
        if up in ("G91",):
            res.incremental = True
            continue
        if up in ("G05", "M15", "M16", "M00", "M30", "G00", "G01", "G02", "G03",
                  "M25", "M01"):
            if up == "M15":
                in_rout = True
                rout_start = (cur_x, cur_y)
            elif up == "M16":
                if in_rout and rout_start and (cur_x, cur_y) != rout_start:
                    # 铣槽路径: 以起点→终点近似为一条槽
                    add_hole(rout_start[0], rout_start[1], cur_tool, is_slot=True,
                             **{"x2": cur_x, "y2": cur_y})
                    res.rout_paths += 1
                    if pending_g in (2, 3):
                        res.notes.append(
                            "检测到圆弧铣槽 (G02/G03), 已按起点→终点弦线近似; "
                            "弧形槽的孔到线判定偏保守")
                in_rout = False
                rout_start = None
            elif up in ("G01", "G02", "G03"):
                pending_g = int(up[1:])
            continue

        m = _TOOL_DEF_RE.match(line)
        if m:
            d = float(m.group(2))
            extra = m.group(3) or ""
            tool_no = int(m.group(1))
            if not in_header:
                res.notes.append(f"刀具 T{tool_no:02d} 在正文中定义 (非 M48 头部), 已接受")
            res.tools[tool_no] = {
                "diameter": d,
                "diameter_mm": d * scale(),
                "feed": (re.search(r"F(" + _NUM + r")", extra).group(1)
                         if re.search(r"F(" + _NUM + r")", extra) else None),
                "hit_count": 0,
            }
            continue

        m = _TOOL_SEL_RE.match(line)
        if m:
            t = int(m.group(1))
            if t == 0:
                cur_tool = None
            else:
                cur_tool = t
                if t not in res.tools:
                    res.notes.append(f"使用了未定义的刀具 T{t:02d}, 孔径未知")
            continue

        # G85 槽孔: X..Y..G85X..Y..
        m = _SLOT_RE.match(line)
        if m:
            x1, y1 = coord(m.group(1)), coord(m.group(2))
            x2, y2 = coord(m.group(3)), coord(m.group(4))
            if None not in (x1, y1, x2, y2):
                add_hole(x1, y1, cur_tool, is_slot=True, **{"x2": x2, "y2": y2})
            continue

        body = line
        gcodes = re.findall(r"G(\d+)", body)
        for g in gcodes:
            gi = int(g)
            if gi in (1, 2, 3):
                pending_g = gi
            elif gi == 90:
                res.incremental = False
            elif gi == 91:
                res.incremental = True
        body = re.sub(r"G\d+", "", body).strip()

        mx = re.search(r"X(" + _NUM + r")", body)
        my = re.search(r"Y(" + _NUM + r")", body)
        mr = re.search(r"R(\d+)", body)
        if not (mx or my or mr):
            continue
        x = coord(mx.group(1)) if mx else None
        y = coord(my.group(1)) if my else None
        if res.incremental:
            x = (cur_x or 0.0) + (x or 0.0) if x is not None else cur_x
            y = (cur_y or 0.0) + (y or 0.0) if y is not None else cur_y
        else:
            x = x if x is not None else cur_x
            y = y if y is not None else cur_y
        if x is None or y is None:
            continue
        if cur_tool is None:
            cur_tool = next(iter(res.tools), 1)
            res.notes.append("孔位出现在选刀之前, 已按第一把刀处理")

        repeat = int(mr.group(1)) if mr else 0
        add_hole(x, y, cur_tool)
        if repeat > 1:
            res.repeat_count += repeat - 1
            if res.incremental:
                # 增量模式: 以本次位移为步距重复
                dx = (x - (cur_x or 0.0)) if cur_x is not None else 0.0
                dy = (y - (cur_y or 0.0)) if cur_y is not None else 0.0
                for k in range(1, repeat):
                    add_hole(x + dx * k, y + dy * k, cur_tool)
            else:
                for _k in range(1, repeat):
                    add_hole(x, y, cur_tool)
        if cur_tool in res.tools:
            res.tools[cur_tool]["hit_count"] = res.tools[cur_tool].get("hit_count", 0) + 1 + max(0, repeat - 1)
        cur_x, cur_y = x, y

    # ---- 统计 ----
    diameters = [h["diameter_mm"] for h in res.holes if h.get("diameter_mm")]
    undefined = [t for t, info in res.tools.items() if not info.get("diameter_mm")]
    if undefined:
        res.notes.append(f"刀具无孔径定义: {undefined}")
    if not res.holes:
        res.notes.append("未解析到任何孔位")

    return {
        "kind": "drill",
        "file": path,
        "name": res.name,
        "unit": res.unit,
        "plating": cur_plating,
        "tools": res.tools,
        "holes": res.holes,
        "hole_count": len(res.holes),
        "slot_count": res.slot_count,
        "rout_slot_count": res.rout_paths,
        "repeat_count": res.repeat_count,
        "min_hole_mm": round(min(diameters), 4) if diameters else None,
        "max_hole_mm": round(max(diameters), 4) if diameters else None,
        "bbox": res.bbox,
        "notes": list(res.notes),
    }
