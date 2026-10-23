"""Gerber RS-274X 解析器 (完整实现)。

相对旧实现的修复:
  * **光圈宏 ``%AM``** 真正展开 (圆/矢量线/矩形/轮廓多边形/正多边形 + ``$n`` 参数
    表达式), 并在解析期扁平化为多边形点集, 下游无需再关心宏
  * **阵列光圈 SR** (``ADD..X<nx>Y<ny>I<dx>J<dy>``) 与 **区域阵列 ``%SR``** 展开为实例
  * **极性**: ``%LPD/%LPC`` 逐图元记录 ``clear``; ``%IP`` 负片图像给出明确告警
  * **真实形状**: 矩形/椭圆/多边形/旋转焊盘保留 ``shape/w/h/rot/points``,
    不再统一按外接圆近似
  * ``%AS`` 轴选择、``%SF`` 缩放因子、``%LN`` 层名、``G74/G75`` 象限模式、
    ``G01/02/03``、``G36/G37`` 区域填充、``D01/D02/D03`` 模态语义
  * 解析质量统计 (:attr:`GerberData.notes`) 供数据质量报告使用

输出结构与旧版兼容 (``kind/name/primitives/bbox/nets/apertures/...``), 并新增
``shape/w/h/rot/points/clear`` 字段。
"""
from __future__ import annotations

import math
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from dfm.io.aperture import (KIND_CIRCLE, KIND_MACRO, KIND_OBLONG, KIND_POLYGON,
                             KIND_RECT, KIND_UNKNOWN, Aperture, parse_aperture,
                             parse_macro_block)

INCH_TO_MM = 25.4
_NUM = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"
_SHAPE_MAP = {KIND_CIRCLE: "circle", KIND_RECT: "rect", KIND_OBLONG: "oval",
              KIND_POLYGON: "poly", KIND_MACRO: "poly", KIND_UNKNOWN: "circle"}


class GerberData:
    """一个 Gerber 文件的解析结果。"""

    def __init__(self, name: str = ""):
        self.name = name
        self.unit = "mm"
        self.int_digits = 3
        self.dec_digits = 3
        self.leading_abs = True        # True=前导零省略(LA, 常见); False=尾随零省略(TZ)
        self.omit_trailing = False
        self.aperture_defs: Dict[int, Aperture] = {}
        self.apertures: Dict[int, dict] = {}
        self.macros: Dict[str, Any] = {}
        self.primitives: List[dict] = []
        self.bbox: List[Optional[float]] = [None, None, None, None]
        self.nets: Dict[str, Set[str]] = {}
        self.ap_usage: Dict[int, int] = {}
        self.attributes_present = False
        self.file_function = ""
        self.file_part = ""
        self.aperture_function = ""
        self.layer_name_attr = ""
        self.image_polarity = "positive"
        self.notes: List[str] = []
        self.clear_count = 0
        self.sr_flash_count = 0
        self.region_count = 0

    def add_point(self, x, y) -> None:
        if x is None or y is None:
            return
        b = self.bbox
        b[0] = x if b[0] is None else min(b[0], x)
        b[1] = y if b[1] is None else min(b[1], y)
        b[2] = x if b[2] is None else max(b[2], x)
        b[3] = y if b[3] is None else max(b[3], y)

    def to_dict(self, path: str = "") -> dict:
        widths = [p.get("width") for p in self.primitives
                  if p.get("type") in ("draw", "arc") and p.get("width")]
        pad_sizes = []
        for p in self.primitives:
            if p.get("type") != "flash":
                continue
            dims = [v for v in (p.get("w"), p.get("h"), p.get("width")) if v]
            if dims:
                pad_sizes.append(min(dims))
        return {
            "kind": "gerber",
            "file": path,
            "name": self.name,
            "unit": self.unit,
            "apertures": self.apertures,
            "primitives": self.primitives,
            "bbox": self.bbox,
            "feature_count": len(self.primitives),
            "attributes_present": self.attributes_present,
            "nets": {k: sorted(v) for k, v in self.nets.items()},
            "min_line_width_mm": round(min(widths), 4) if widths else None,
            "min_pad_mm": round(min(pad_sizes), 4) if pad_sizes else None,
            "aperture_count": len(self.apertures),
            "file_function": self.file_function,
            "file_part": self.file_part,
            "aperture_function": self.aperture_function,
            "layer_name_attr": self.layer_name_attr,
            "image_polarity": self.image_polarity,
            "clear_count": self.clear_count,
            "region_count": self.region_count,
            "sr_flash_count": self.sr_flash_count,
            "notes": _dedupe(self.notes),
        }


def _dedupe(items: List[str], limit: int = 20) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for it in items:
        if it in seen:
            continue
        seen.add(it)
        out.append(it)
        if len(out) >= limit:
            break
    return out


def _flatten_macro_geom(geom) -> Tuple[str, float, float, List[Tuple[float, float]]]:
    """把宏几何扁平化为多边形点集 (外环), 返回 (shape, w, h, points)。"""
    from shapely.geometry import MultiPolygon, Polygon

    polys: List[Polygon] = []
    if isinstance(geom, MultiPolygon):
        polys = [g for g in geom.geoms if not g.is_empty]
    elif isinstance(geom, Polygon):
        polys = [geom]
    elif geom is not None and not geom.is_empty:
        try:
            buffered = geom.buffer(0)
            if isinstance(buffered, MultiPolygon):
                polys = [g for g in buffered.geoms if not g.is_empty]
            elif isinstance(buffered, Polygon):
                polys = [buffered]
        except Exception:
            polys = []
    if not polys:
        return "circle", 0.0, 0.0, []
    # 取面积最大的外环作为焊盘轮廓 (内孔在 DFM 判定中按实心处理, 偏保守)
    poly = max(polys, key=lambda g: g.area)
    pts = [(float(x), float(y)) for x, y in poly.exterior.coords]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return "poly", (max(xs) - min(xs)), (max(ys) - min(ys)), pts


class _Parser:
    def __init__(self, path: str, name: Optional[str]):
        self.path = path
        self.res = GerberData(name or os.path.basename(path))
        # 状态
        self.cur_ap: Optional[int] = None
        self.cur_x = 0.0
        self.cur_y = 0.0
        self.interp = 1                 # 1=线性 2=顺时针弧 3=逆时针弧
        self.multi_quadrant = True      # G75 (默认)
        self.cur_op = 1
        self.polarity_clear = False     # %LPC
        self.image_negative = False     # %IPN / I
        self.in_region = False
        self.region_pts: List[Tuple[float, float]] = []
        self.region_sr: Optional[Dict[str, float]] = None
        self.cur_net = ""
        self.cur_refdes = ""
        self.cur_pin = ""
        self.sx = 1.0                   # %SF 缩放
        self.sy = 1.0
        self.ax_sign = 1.0              # %AS 轴选择
        self.ay_sign = 1.0
        self.swap_axes = False
        self._pending_x = 0.0           # G74 单象限推断 I/J 符号用的终点
        self._pending_y = 0.0
        self._macro_buf: Optional[List[str]] = None
        self._macro_name = ""

    # ---------------- 主循环 ----------------
    def parse(self) -> GerberData:
        with open(self.path, encoding="utf-8", errors="ignore") as f:
            content = f.read()
        # Gerber 以 '*' 结束指令, '%' 包裹扩展码; 按行解析同时处理跨行宏
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if self._macro_buf is not None:
                self._macro_buf.append(line)
                if line.rstrip().endswith("%") or "%" in line:
                    self._finish_macro(line)
                continue
            if line.startswith("%AM"):
                self._start_macro(line)
                continue
            if line.startswith("%"):
                self._extended(line)
                continue
            self._function(line)
        if self._macro_buf is not None:
            self.res.notes.append("文件结尾处光圈宏未闭合, 已按现有内容解析")
            self._finish_macro("")
        return self.res

    # ---------------- 宏 ----------------
    def _start_macro(self, line: str) -> None:
        m = re.match(r"%AM([A-Za-z_$][\w$.]*)\*(.*)$", line)
        if not m:
            self.res.notes.append(f"无法识别的光圈宏定义: {line[:40]}")
            self._macro_buf = None
            return
        self._macro_name = m.group(1)
        self._macro_buf = [m.group(2)]
        if line.count("%") >= 2 and line.rstrip().endswith("%"):
            self._finish_macro(line)

    def _finish_macro(self, line: str) -> None:
        body = list(self._macro_buf or [])
        # 去掉尾部的 '%'
        if body:
            body[-1] = body[-1].rstrip().rstrip("%")
        macro = parse_macro_block(self._macro_name, body)
        self.res.macros[self._macro_name] = macro
        if macro.parse_errors:
            self.res.notes.extend(macro.parse_errors[:3])
        self._macro_buf = None
        self._macro_name = ""

    # ---------------- 扩展码 ----------------
    def _extended(self, line: str) -> None:
        body = line.strip("%").rstrip("*").strip()
        if not body:
            return
        head = body.split(",")[0].strip()
        r = self.res

        if body.startswith("FS"):
            # %FS<L|T><A|I>X<int><dec>Y<int><dec>*%
            #   L=省略前导零(常规) / T=省略尾随零;  A=绝对坐标 / I=增量坐标
            m = re.search(r"([LT])([AI])X(\d)(\d)Y(\d)(\d)", body)
            if m:
                self.res.omit_trailing = (m.group(1) == "T")
                self.res.leading_abs = not self.res.omit_trailing
                self.res.int_digits = int(m.group(3))
                self.res.dec_digits = int(m.group(4))
                if m.group(2) == "I":
                    r.notes.append("%FS 为增量坐标模式(I), 已按绝对坐标解析, 请复核")
                if self.res.omit_trailing:
                    r.notes.append("坐标为尾随零省略(T), 已按整数位补齐解析")
                return
            m = re.search(r"X(\d)(\d)Y(\d)(\d)", body)
            if m:
                self.res.int_digits = int(m.group(1))
                self.res.dec_digits = int(m.group(2))
                r.notes.append("%FS 缺少零省略/坐标模式声明, 按前导零省略(LA)解析")
            else:
                r.notes.append(f"无法解析的 %FS: {body}")
            return
        if body.startswith("MO"):
            up = body.upper()
            r.unit = "inch" if "IN" in up else "mm"
            return
        if body.startswith("AD"):
            scale = INCH_TO_MM if r.unit == "inch" else 1.0
            ap = parse_aperture(body, macros=r.macros, scale=scale)
            if ap is None:
                r.notes.append(f"无法解析的光圈定义: {body[:40]}")
                return
            r.aperture_defs[ap.dcode] = ap
            r.apertures[ap.dcode] = {
                "shape": {KIND_CIRCLE: "C", KIND_RECT: "R", KIND_OBLONG: "O",
                          KIND_POLYGON: "P", KIND_MACRO: "AM"}.get(ap.kind, "?"),
                "params": [v for v in (ap.w, ap.h, ap.hole) if v],
                "raw": ap.raw, "kind": ap.kind, "w": ap.w, "h": ap.h,
                "rot": ap.rot, "vertices": ap.vertices,
                "sr": dict(ap.sr) if ap.sr else None, "macro": ap.macro_name,
                "note": ap.note,
            }
            if ap.note:
                r.notes.append(f"光圈 D{ap.dcode}: {ap.note}")
            return
        if body.startswith("LP"):
            self.polarity_clear = body[2:3].upper() == "C"
            return
        if body.startswith("IP"):
            mode = body[2:3].upper()
            self.image_negative = mode == "N"
            r.image_polarity = "negative" if self.image_negative else "positive"
            if self.image_negative:
                r.notes.append("整幅图像为负片(%IPN): 铜箔与挖空语义相反, "
                               "间距/短路结论需人工复核")
            return
        if body.startswith("AS"):
            m = re.match(r"AS([AB])([XY])([AB])([XY])", body)
            if m:
                self.swap_axes = (m.group(2) == "Y")
                self.ax_sign = -1.0 if m.group(1) == "B" else 1.0
                self.ay_sign = -1.0 if m.group(3) == "B" else 1.0
                if self.swap_axes:
                    r.notes.append("检测到 %AS 轴交换, 坐标已按轴选择映射")
            return
        if body.startswith("SF"):
            m = re.match(r"SF(?:A(" + _NUM + r"))?(?:B(" + _NUM + r"))?", body)
            if m:
                self.sx = float(m.group(1) or 1.0)
                self.sy = float(m.group(2) or 1.0)
                if abs(self.sx - 1.0) > 1e-9 or abs(self.sy - 1.0) > 1e-9:
                    r.notes.append(f"检测到 %SF 缩放 A={self.sx} B={self.sy}, 坐标已按比例换算")
            return
        if body.startswith("LN"):
            r.layer_name_attr = body[2:].strip()
            return
        if body.startswith("TF"):
            parts = [p.strip() for p in body.split(",")]
            if parts[0] == "TF.FileFunction":
                r.file_function = ",".join(parts[1:])
            elif parts[0] == "TF.Part":
                r.file_part = ",".join(parts[1:])
            elif parts[0] == "TF.FilePolarity":
                pol = ",".join(parts[1:]).upper()
                if pol == "NEGATIVE":
                    self.image_negative = True
                    r.image_polarity = "negative"
                    r.notes.append("TF.FilePolarity=Negative: 本层为负片 (常见于内层平面), "
                                   "铜箔为整面减去 clear 图形")
            return
        if body.startswith("TO"):
            parts = [p.strip() for p in body.split(",")]
            if parts[0] == "TO.N":
                self.cur_net = ",".join(parts[1:]).strip()
                self.cur_refdes = ""
                self.cur_pin = ""
                r.attributes_present = True
            elif parts[0] == "TO.P" or parts[0] == "TO.C":
                if len(parts) >= 2:
                    self.cur_refdes = parts[1]
                if len(parts) >= 3:
                    self.cur_pin = parts[2]
            return
        if body.startswith("TA"):
            parts = [p.strip() for p in body.split(",")]
            if parts[0] == "TA.AperFunction":
                r.aperture_function = ",".join(parts[1:])
            return
        if body.startswith("TD"):
            if body.strip() == "TD":
                self.cur_net = self.cur_refdes = self.cur_pin = ""
                r.aperture_function = ""
            return
        if body.startswith("SR"):
            # 区域阵列: %SRX2Y3I1J1*% 开始, %SR*% 结束
            m = re.match(r"SR(?:X(\d+)Y(\d+)I(" + _NUM + r")J(" + _NUM + r"))?", body)
            if m and m.group(1):
                scale = INCH_TO_MM if r.unit == "inch" else 1.0
                self.region_sr = {"nx": int(m.group(1)), "ny": int(m.group(2)),
                                  "dx": float(m.group(3)) * scale,
                                  "dy": float(m.group(4)) * scale}
            else:
                self.region_sr = None
            return
        if head.startswith(("G70", "G71", "G04", "M0", "M1", "M2")):
            if head == "G70":
                r.unit = "inch"
            elif head == "G71":
                r.unit = "mm"
            return
        # 其余未支持的扩展码: 记录一次即可
        key = head[:2]
        if key not in ("G3", "G7", "M0"):
            self.res.notes.append(f"忽略未支持的扩展码: %{body[:32]}")

    # ---------------- 功能码 ----------------
    def _function(self, line: str) -> None:
        cmd = line.rstrip("*").strip()
        if not cmd:
            return
        if cmd.startswith("G36"):
            self.in_region = True
            self.region_pts = []
            cmd = cmd[3:].strip()
        elif cmd.startswith("G37"):
            self._flush_region()
            cmd = cmd[3:].strip()
        if cmd.startswith("M"):
            return
        gcodes = [int(g) for g in re.findall(r"G(\d+)", cmd)]
        if 4 in gcodes:
            return
        for g in gcodes:
            if g in (1, 2, 3):
                self.interp = g
            elif g == 74:
                self.multi_quadrant = False
            elif g == 75:
                self.multi_quadrant = True
            elif g == 70:
                self.res.unit = "inch"
            elif g == 71:
                self.res.unit = "mm"

        for dm in re.finditer(r"D(\d+)", cmd):
            n = int(dm.group(1))
            if n >= 10:
                self.cur_ap = n
        dops = [int(dm.group(1)) for dm in re.finditer(r"D(\d+)", cmd)
                if int(dm.group(1)) in (1, 2, 3)]
        if dops:
            self.cur_op = dops[-1]

        mx = re.search(r"X(" + _NUM + r")", cmd)
        my = re.search(r"Y(" + _NUM + r")", cmd)
        mi = re.search(r"I(" + _NUM + r")", cmd)
        mj = re.search(r"J(" + _NUM + r")", cmd)
        if not (mx or my or mi or mj):
            return

        scale = INCH_TO_MM if self.res.unit == "inch" else 1.0
        x = self._coord(mx.group(1), scale) if mx else None
        y = self._coord(my.group(1), scale) if my else None
        i_off = self._coord(mi.group(1), scale) if mi else None
        j_off = self._coord(mj.group(1), scale) if mj else None

        nx = x if x is not None else self.cur_x
        ny = y if y is not None else self.cur_y
        nx, ny = self._apply_axis(nx, ny)
        # G74 单象限需要终点坐标来推断 I/J 符号
        self._pending_x, self._pending_y = nx, ny
        if not self.multi_quadrant and (i_off is not None or j_off is not None):
            i_off, j_off = self._single_quadrant(i_off, j_off)

        if self.in_region:
            if self.cur_op in (1, 2):
                self.region_pts.append((nx, ny))
            self.cur_x, self.cur_y = nx, ny
            return

        if self.cur_op == 3:
            self._flash(nx, ny)
        elif self.cur_op == 1:
            if self.interp in (2, 3) and (i_off is not None or j_off is not None):
                self._arc(nx, ny, (i_off or 0.0) * scale, (j_off or 0.0) * scale)
            else:
                self._draw(nx, ny)
        self._record_net()
        self.cur_x, self.cur_y = nx, ny

    def _coord(self, raw: str, scale: float) -> Optional[float]:
        """按 %FS 位宽换算坐标。

        支持两种写法:
          * 显式小数点: ``X1.5`` → 1.5
          * 隐含小数 (标准): ``X0150000`` 且 FS=LA X34 → 15.0 mm
        """
        if raw is None:
            return None
        raw = raw.strip()
        if not raw:
            return None
        unit_scale = INCH_TO_MM if self.res.unit == "inch" else 1.0
        if "." in raw:
            try:
                v = float(raw)
            except ValueError:
                return None
            return v * unit_scale
        neg = raw.startswith("-")
        digits = raw.lstrip("+-")
        if not digits.isdigit():
            return None
        dec = self.res.dec_digits or 3
        v = int(digits) / (10.0 ** dec)
        if neg:
            v = -v
        if self.res.leading_abs is False:          # 尾随零省略 (TZ): 需左对齐补齐
            total = (self.res.int_digits or 0) + dec
            if len(digits) < total:
                v = int(digits.ljust(total, "0")) / (10.0 ** dec)
                if neg:
                    v = -v
        return v * unit_scale

    def _apply_axis(self, x: float, y: float) -> Tuple[float, float]:
        x = x * self.ax_sign * self.sx
        y = y * self.ay_sign * self.sy
        if self.swap_axes:
            x, y = y, x
        return x, y

    def _single_quadrant(self, i: Optional[float], j: Optional[float]):
        """G74 单象限模式: I/J 给的是绝对值, 需按"起止点到圆心等距"补符号。"""
        if i is None or j is None:
            return i, j
        ai, aj = abs(i), abs(j)
        best = None
        best_err = float("inf")
        for si in (1.0, -1.0):
            for sj in (1.0, -1.0):
                cx = self.cur_x + ai * si
                cy = self.cur_y + aj * sj
                r0 = math.hypot(self.cur_x - cx, self.cur_y - cy)
                r1 = math.hypot(self._pending_x - cx, self._pending_y - cy)
                err = abs(r0 - r1)
                if err < best_err:
                    best_err, best = err, (ai * si, aj * sj)
        return best if best else (ai, aj)

    # ---------------- 图元 ----------------
    def _aperture(self) -> Optional[Aperture]:
        if self.cur_ap is None:
            return None
        return self.res.aperture_defs.get(self.cur_ap)

    def _stroke_width(self, ap: Optional[Aperture]) -> Optional[float]:
        if ap is None:
            return None
        w = ap.stroke_width
        return w if w > 0 else None

    def _base_prim(self, **kw) -> dict:
        prim = {
            "net": self.cur_net or "", "refdes": self.cur_refdes or "",
            "pin": self.cur_pin or "", "layer": self.res.name,
            "clear": bool(self.polarity_clear) != bool(self.image_negative),
            "aperture": self.cur_ap,
        }
        if prim["clear"]:
            self.res.clear_count += 1
        prim.update(kw)
        return prim

    def _draw(self, nx: float, ny: float) -> None:
        ap = self._aperture()
        if ap is None:
            self.res.primitives.append(self._base_prim(
                type="error", x1=self.cur_x, y1=self.cur_y, x2=nx, y2=ny,
                error="draw_no_aperture"))
            return
        w = self._stroke_width(ap)
        self.res.primitives.append(self._base_prim(
            type="draw", x1=self.cur_x, y1=self.cur_y, x2=nx, y2=ny,
            width=w, shape=ap.kind if ap.kind != KIND_MACRO else "poly",
            square_cap=(ap.kind == KIND_RECT)))
        self._use_ap(ap)
        self.res.add_point(self.cur_x, self.cur_y)
        self.res.add_point(nx, ny)

    def _arc(self, nx: float, ny: float, i_off: float, j_off: float) -> None:
        ap = self._aperture()
        w = self._stroke_width(ap)
        self.res.primitives.append(self._base_prim(
            type="arc", x1=self.cur_x, y1=self.cur_y, x2=nx, y2=ny,
            i=i_off, j=j_off, cw=(self.interp == 2), width=w,
            multi_quadrant=self.multi_quadrant))
        if ap is not None:
            self._use_ap(ap)
        self.res.add_point(self.cur_x, self.cur_y)
        self.res.add_point(nx, ny)
        cx, cy = self.cur_x + i_off, self.cur_y + j_off
        r = math.hypot(self.cur_x - cx, self.cur_y - cy)
        self.res.add_point(cx - r, cy - r)
        self.res.add_point(cx + r, cy + r)

    def _flash(self, x: float, y: float) -> None:
        ap = self._aperture()
        if ap is None:
            self.res.primitives.append(self._base_prim(
                type="error", x1=x, y1=y, x2=x, y2=y, error="flash_no_aperture"))
            return
        offsets = ap.instances()
        for ox, oy in offsets:
            fx, fy = x + ox, y + oy
            prim = self._flash_prim(ap, fx, fy)
            if prim is not None:
                self.res.primitives.append(prim)
                self.res.add_point(fx, fy)
                self._expand_bbox_by_aperture(fx, fy, ap)
        if len(offsets) > 1:
            self.res.sr_flash_count += len(offsets)
        self._use_ap(ap)

    def _expand_bbox_by_aperture(self, x: float, y: float, ap: Aperture) -> None:
        """包围盒外扩半个光圈尺寸, 避免渲染/判定裁边。"""
        r = max(ap.max_size, ap.w, ap.h) / 2.0 if ap else 0.0
        if r <= 0:
            return
        self.res.add_point(x - r, y - r)
        self.res.add_point(x + r, y + r)

    def _flash_prim(self, ap: Aperture, x: float, y: float) -> Optional[dict]:
        base = self._base_prim(type="flash", x1=x, y1=y, x2=x, y2=y)
        if ap.kind == KIND_MACRO and ap.macro is not None:
            geom, notes = ap.geometry(x, y)
            for n in notes[:2]:
                if n not in self.res.notes:
                    self.res.notes.append(f"光圈 D{ap.dcode}: {n}")
            if geom is None:
                base.update(type="error", error="macro_expand_failed",
                            width=ap.max_size or None)
                return base
            shape, w, h, pts = _flatten_macro_geom(geom)
            base.update(shape=shape, w=w, h=h, rot=0.0,
                        width=(min(w, h) if min(w, h) > 0 else max(w, h)),
                        points=pts, macro=ap.macro_name)
            return base
        shp = ap.shape(x, y)
        if shp is None:
            base.update(type="error", error="aperture_no_size", width=None)
            return base
        base.update(shape=_SHAPE_MAP.get(ap.kind, "circle"),
                    w=ap.w, h=ap.h or ap.w, rot=ap.rot,
                    width=(min(ap.w, ap.h or ap.w) if ap.w else None),
                    hole=ap.hole or None)
        if ap.kind == KIND_POLYGON:
            base["points"] = list(shp.points)
        return base

    def _use_ap(self, ap: Aperture) -> None:
        self.res.ap_usage[ap.dcode] = self.res.ap_usage.get(ap.dcode, 0) + 1

    def _flush_region(self) -> None:
        self.in_region = False
        pts = self.region_pts
        self.region_pts = []
        if len(pts) < 3:
            return
        offsets = [(0.0, 0.0)]
        if self.region_sr:
            nx = max(1, int(self.region_sr.get("nx", 1)))
            ny = max(1, int(self.region_sr.get("ny", 1)))
            dx = self.region_sr.get("dx", 0.0)
            dy = self.region_sr.get("dy", 0.0)
            offsets = [(i * dx, j * dy) for i in range(nx) for j in range(ny)]
        for ox, oy in offsets:
            shifted = [(px + ox, py + oy) for px, py in pts]
            xs = [p[0] for p in shifted]
            ys = [p[1] for p in shifted]
            prim = self._base_prim(
                type="region", points=shifted,
                x1=min(xs), y1=min(ys), x2=max(xs), y2=max(ys), width=None)
            self.res.primitives.append(prim)
            self.res.region_count += 1
            for p in shifted:
                self.res.add_point(p[0], p[1])
        self._record_net()

    def _record_net(self) -> None:
        if not self.cur_net:
            return
        node = f"{self.cur_refdes}.{self.cur_pin}" if (self.cur_refdes and self.cur_pin) else None
        if node:
            self.res.nets.setdefault(self.cur_net, set()).add(node)


def parse(path: str, layer_name: Optional[str] = None) -> dict:
    """解析一个 Gerber 文件, 返回与其它解析器一致的 dict。"""
    return _Parser(path, layer_name).parse().to_dict(path)


def looks_like_gerber(path: str) -> bool:
    """通过头部特征判断是否为 Gerber 文件。"""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            head = f.read(8192)
    except OSError:
        return False
    return any(tok in head for tok in ("%FS", "%MO", "%ADD", "%AM", "G04", "G01", "D02"))
