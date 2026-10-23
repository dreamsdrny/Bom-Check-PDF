"""Gerber 光圈 (Aperture) 与光圈宏 (Aperture Macro) 模型。

旧实现只认 ``C/R/O`` 且取 ``min(w,h)``, 宏光圈 (``%AM``) 直接跳过 →
金手指、异形焊盘、Mark、阵列光圈 (SR) 全部读不出真实尺寸, 导致误判。

本模块提供:
  * 标准光圈: 圆 C / 矩形 R / 椭圆 O / 多边形 P (含旋转与中心孔)
  * **阵列光圈 SR**: ``ADD10R,[REDACTED-UNKNOWN]X2Y3I1J1`` → 展开为 2x3 个实例
  * **光圈宏 AM**: 支持宏图元 1(圆)/2,20(矢量线)/21(矩形-中心)/22(矩形-左下)/
    4(轮廓多边形)/5(正多边形), 参数表达式 ``$1..$9`` 与 ``+ - x / ( )``
    (自带安全表达式求值器, 不使用 eval)
  * 输出 :class:`Shape` / shapely 几何, 供 :mod:`dfm.geom` 精确判定
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dfm.geom.shapes import Shape

KIND_CIRCLE = "circle"
KIND_RECT = "rect"
KIND_OBLONG = "oblong"
KIND_POLYGON = "polygon"
KIND_MACRO = "macro"
KIND_UNKNOWN = "unknown"

#: 宏图元代码 → 名称 (6/7/8 为对位标记, DFM 判定中按圆近似并给出提示)
MACRO_PRIMS = {1: "circle", 2: "vector", 20: "vector", 21: "center_rect",
               22: "lower_left_rect", 4: "outline", 5: "regular_polygon",
               6: "moire", 7: "crosshair", 8: "thermal"}

_TOKEN_RE = re.compile(r"\s*(\$[1-9]|\d+\.?\d*(?:[eE][-+]?\d+)?|[+\-xX/()]|\d+)")


# ============================================================
# 宏表达式求值 (安全, 不使用 eval)
# ============================================================
def eval_expr(text: Any, params: Optional[Dict[int, float]] = None) -> Optional[float]:
    """求值光圈宏表达式: 支持数字、``$1..$9``、``+ - x X * / ( )``。

    ``x``/``X`` 在 Gerber 宏中是乘号; 无法求值时返回 None (调用方按未知处理)。
    """
    params = params or {}
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    tokens: List[Any] = []
    s = str(text).strip()
    if not s:
        return None
    pos = 0
    while pos < len(s):
        if s[pos].isspace():
            pos += 1
            continue
        m = _TOKEN_RE.match(s, pos)
        if not m:
            return None
        tok = m.group(1)
        pos = m.end()
        if tok.startswith("$"):
            idx = int(tok[1:])
            if idx not in params:
                return None
            tokens.append(float(params[idx]))
        elif tok in ("+", "-"):
            tokens.append(tok)
        elif tok in ("x", "X", "*"):
            tokens.append("*")
        elif tok == "/":
            tokens.append("/")
        elif tok in ("(", ")"):
            tokens.append(tok)
        else:
            try:
                tokens.append(float(tok))
            except ValueError:
                return None
    try:
        return _parse_expr(tokens, 0)[0]
    except Exception:
        return None


def _parse_expr(tokens: List[Any], i: int) -> Tuple[float, int]:
    """加减层"""
    val, i = _parse_term(tokens, i)
    while i < len(tokens) and tokens[i] in ("+", "-"):
        op = tokens[i]
        rhs, i = _parse_term(tokens, i + 1)
        val = val + rhs if op == "+" else val - rhs
    return val, i


def _parse_term(tokens: List[Any], i: int) -> Tuple[float, int]:
    """乘除层"""
    val, i = _parse_unary(tokens, i)
    while i < len(tokens) and tokens[i] in ("*", "/"):
        op = tokens[i]
        rhs, i = _parse_unary(tokens, i + 1)
        if op == "*":
            val = val * rhs
        else:
            val = val / rhs if abs(rhs) > 1e-12 else float("inf")
    return val, i


def _parse_unary(tokens: List[Any], i: int) -> Tuple[float, int]:
    if i < len(tokens) and tokens[i] == "-":
        val, i = _parse_unary(tokens, i + 1)
        return -val, i
    if i < len(tokens) and tokens[i] == "+":
        return _parse_unary(tokens, i + 1)
    return _parse_atom(tokens, i)


def _parse_atom(tokens: List[Any], i: int) -> Tuple[float, int]:
    if i >= len(tokens):
        raise ValueError("表达式意外结束")
    tok = tokens[i]
    if tok == "(":
        val, i = _parse_expr(tokens, i + 1)
        if i < len(tokens) and tokens[i] == ")":
            i += 1
        return val, i
    if isinstance(tok, float):
        return tok, i + 1
    raise ValueError(f"无法解析的记号: {tok}")


# ============================================================
# 光圈宏
# ============================================================
@dataclass
class MacroPrim:
    """一条宏图元定义 (字段保持字符串, 求值时代入 $n)。"""

    code: int
    fields: List[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return MACRO_PRIMS.get(self.code, f"prim{self.code}")


class ApertureMacro:
    """解析后的光圈宏。"""

    def __init__(self, name: str, body: Sequence[str]):
        self.name = name
        self.raw_lines = list(body)
        self.prims: List[MacroPrim] = []
        self.parse_errors: List[str] = []
        self._parse()

    def _parse(self) -> None:
        # 宏体以 '*' 分隔图元, 可能跨行
        joined = "".join(self.raw_lines).replace("\n", "")
        for chunk in joined.split("*"):
            chunk = chunk.strip()
            if not chunk or chunk.startswith("G04"):
                continue
            parts = [p.strip() for p in chunk.split(",") if p.strip() != ""]
            if not parts:
                continue
            if parts[0].startswith("0"):
                continue                      # Gerber 宏中以 0 开头的行是注释
            try:
                code = int(float(parts[0]))
            except ValueError:
                self.parse_errors.append(f"无法识别的宏图元: {chunk[:40]}")
                continue
            self.prims.append(MacroPrim(code=code, fields=parts[1:]))

    def geom(self, params: Optional[Dict[int, float]] = None, scale: float = 1.0):
        """按参数实例化宏, 返回 shapely 几何 (已含 exposure=0 的挖除)。"""
        from shapely.ops import unary_union

        params = params or {}
        dark: List[Any] = []
        clear: List[Any] = []
        unsupported: List[str] = []
        for prim in self.prims:
            g, expose, note = self._prim_geom(prim, params, scale)
            if note:
                unsupported.append(note)
            if g is None:
                continue
            (dark if expose else clear).append(g)
        if not dark:
            return None, unsupported
        result = unary_union(dark) if len(dark) > 1 else dark[0]
        if clear:
            cut = unary_union(clear) if len(clear) > 1 else clear[0]
            try:
                result = result.difference(cut)
            except Exception:
                pass
        return result, unsupported

    def _prim_geom(self, prim: MacroPrim, params: Dict[int, float], scale: float):
        """返回 (geometry, exposure, note)"""
        from shapely.affinity import rotate as _rot, translate as _tr
        from shapely.geometry import LineString, Point, Polygon

        f = prim.fields

        def num(i: int, default: float = 0.0) -> float:
            if i >= len(f):
                return default
            v = eval_expr(f[i], params)
            return default if v is None else v * scale

        def flag(i: int, default: int = 1) -> int:
            if i >= len(f):
                return default
            v = eval_expr(f[i], params)
            return default if v is None else int(round(v))

        code = prim.code
        try:
            if code == 1:                                   # 圆
                expose = flag(0)
                d = num(1)
                x, y = num(2), num(3)
                if d <= 0:
                    return None, expose, ""
                return _tr(Point(0, 0).buffer(d / 2.0, quad_segs=12), x, y), bool(expose), ""
            if code in (2, 20):                             # 矢量线
                expose = flag(0)
                w = num(1)
                x1, y1, x2, y2 = num(2), num(3), num(4), num(5)
                rot = num(6)
                g = LineString([(x1, y1), (x2, y2)]).buffer(
                    max(w / 2.0, 1e-9), quad_segs=4, cap_style=2)
                if rot:
                    g = _rot(g, rot, origin=(0, 0))
                return g, bool(expose), ""
            if code == 21:                                  # 矩形 (中心基准)
                expose = flag(0)
                w, h = num(1), num(2)
                x, y, rot = num(3), num(4), num(5)
                if w <= 0 or h <= 0:
                    return None, expose, ""
                g = Polygon([(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)])
                if rot:
                    g = _rot(g, rot, origin=(0, 0))
                return _tr(g, x, y), bool(expose), ""
            if code == 22:                                  # 矩形 (左下基准)
                expose = flag(0)
                w, h = num(1), num(2)
                x, y, rot = num(3), num(4), num(5)
                if w <= 0 or h <= 0:
                    return None, expose, ""
                g = Polygon([(0, 0), (w, 0), (w, h), (0, h)])
                if rot:
                    g = _rot(g, rot, origin=(0, 0))
                return _tr(g, x, y), bool(expose), ""
            if code == 4:                                   # 轮廓多边形
                expose = flag(0)
                n = int(round(num(1)))
                pts: List[Tuple[float, float]] = []
                for k in range(n):
                    px, py = num(2 + k * 2), num(3 + k * 2)
                    pts.append((px, py))
                rot = num(2 + n * 2)
                if len(pts) < 3:
                    return None, expose, ""
                g = Polygon(pts)
                if not g.is_valid:
                    g = g.buffer(0)
                if rot:
                    g = _rot(g, rot, origin=(0, 0))
                return g, bool(expose), ""
            if code == 5:                                   # 正多边形
                expose = flag(0)
                n = int(round(num(1)))
                x, y, d, rot = num(2), num(3), num(4), num(5)
                if n < 3 or d <= 0:
                    return None, expose, ""
                r = d / 2.0
                pts = []
                for k in range(n):
                    a = math.radians(rot + k * 360.0 / n)
                    pts.append((x + r * math.cos(a), y + r * math.sin(a)))
                g = Polygon(pts)
                return (g if g.is_valid else g.buffer(0)), bool(expose), ""
            if code in (6, 7, 8):                           # 对位标记 (DFM 判定少见)
                return None, 1, f"宏图元 {code} ({prim.name}) 未展开, 已忽略"
            return None, 1, f"未知宏图元 {code}, 已忽略"
        except Exception as exc:
            return None, 1, f"宏图元 {code} 展开失败: {exc}"


# ============================================================
# 光圈定义
# ============================================================
@dataclass
class Aperture:
    """一个 ``%ADD`` 光圈定义 (含阵列与宏实例化)。"""

    dcode: int
    kind: str = KIND_UNKNOWN
    w: float = 0.0                       # mm (圆=直径; 矩形/椭圆=宽)
    h: float = 0.0                       # mm (矩形/椭圆=高)
    hole: float = 0.0                    # mm 中心孔 (C/R/O 的第二参数)
    vertices: int = 0                    # P 型多边形顶点数
    rot: float = 0.0                     # deg
    macro_name: str = ""
    macro: Optional[ApertureMacro] = None
    macro_params: List[float] = field(default_factory=list)
    sr: Optional[Dict[str, float]] = None    # {nx, ny, dx, dy} 阵列
    raw: str = ""
    note: str = ""

    # ---------------- 尺寸 ----------------
    @property
    def min_size(self) -> float:
        """最小外接尺寸 (mm): 用于线宽类判定"""
        sizes = [v for v in (self.w, self.h) if v > 0]
        return min(sizes) if sizes else 0.0

    @property
    def max_size(self) -> float:
        sizes = [v for v in (self.w, self.h) if v > 0]
        return max(sizes) if sizes else 0.0

    @property
    def stroke_width(self) -> float:
        """作为走线光圈时的等效线宽 (矩形/椭圆取短边, 宏取外接尺寸)"""
        if self.kind == KIND_CIRCLE:
            return self.w
        if self.kind == KIND_MACRO:
            return self.max_size
        return self.min_size or self.w

    def instances(self) -> List[Tuple[float, float]]:
        """阵列光盘的实例偏移 (相对闪点), 无阵列时返回 [(0,0)]"""
        if not self.sr:
            return [(0.0, 0.0)]
        nx = max(1, int(self.sr.get("nx", 1)))
        ny = max(1, int(self.sr.get("ny", 1)))
        dx = float(self.sr.get("dx", 0.0))
        dy = float(self.sr.get("dy", 0.0))
        return [(i * dx, j * dy) for i in range(nx) for j in range(ny)]

    def shape(self, dx: float = 0.0, dy: float = 0.0) -> Optional[Shape]:
        """基本形状 (不含宏); 宏光圈返回 None, 请用 :meth:`geometry`。"""
        if self.kind == KIND_CIRCLE:
            return Shape.circle(dx, dy, self.w) if self.w > 0 else None
        if self.kind == KIND_RECT:
            return Shape.rect(dx, dy, self.w, self.h or self.w, self.rot)
        if self.kind == KIND_OBLONG:
            return Shape.oblong(dx, dy, self.w, self.h or self.w, self.rot)
        if self.kind == KIND_POLYGON:
            n = max(3, int(self.vertices or 4))
            r = (self.w or 0.0) / 2.0
            if r <= 0:
                return None
            pts = [(dx + r * math.cos(math.radians(self.rot + k * 360.0 / n)),
                    dy + r * math.sin(math.radians(self.rot + k * 360.0 / n)))
                   for k in range(n)]
            return Shape.polygon(pts)
        return None

    def geometry(self, dx: float = 0.0, dy: float = 0.0, scale: float = 1.0):
        """完整几何 (含宏展开与阵列), 返回 (geometry|None, notes)。"""
        from shapely.affinity import translate as _tr
        from shapely.ops import unary_union

        notes: List[str] = []
        base = None
        if self.kind == KIND_MACRO and self.macro is not None:
            params = {i + 1: v for i, v in enumerate(self.macro_params)}
            base, macro_notes = self.macro.geom(params, scale=scale)
            notes.extend(macro_notes)
        else:
            shp = self.shape(0.0, 0.0)
            if shp is not None:
                base = shp.geom
        if base is None:
            if self.note:
                notes.append(self.note)
            return None, notes
        if self.hole > 0:                     # 中心孔 (C/R/O 的第二参数)
            try:
                from shapely.geometry import Point

                base = base.difference(Point(0, 0).buffer(self.hole / 2.0, quad_segs=10))
            except Exception:
                pass
        if self.kind not in (KIND_CIRCLE, KIND_MACRO) and self.rot:
            from shapely.affinity import rotate as _rot

            base = _rot(base, self.rot, origin=(0, 0))
        offs = self.instances()
        if len(offs) > 1:
            parts = [_tr(base, dx + ox * scale, dy + oy * scale) for ox, oy in offs]
            base = unary_union(parts)
        elif dx or dy:
            base = _tr(base, dx, dy)
        return base, notes

    def describe(self) -> str:
        if self.kind == KIND_MACRO:
            return f"macro:{self.macro_name}({','.join(f'{v:g}' for v in self.macro_params)})"
        if self.kind == KIND_CIRCLE:
            return f"C{self.w:g}" + (f"X{self.hole:g}" if self.hole else "")
        if self.kind == KIND_POLYGON:
            return f"P{self.w:g}X{self.vertices}X{self.rot:g}"
        return (f"{self.kind[:1].upper()}{self.w:g}X{self.h:g}"
                + (f"X{self.hole:g}" if self.hole else ""))


# ============================================================
# 解析
# ============================================================
_NUM = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parse_macro_block(name: str, body_lines: Sequence[str]) -> ApertureMacro:
    """解析 ``%AMNAME*<body>*%``。"""
    return ApertureMacro(name, body_lines)


def parse_aperture(block: str, macros: Optional[Dict[str, ApertureMacro]] = None,
                   scale: float = 1.0) -> Optional[Aperture]:
    """解析一条 ``ADD`` 定义 (不含首尾 %/*), 支持阵列与宏。

    形如::

        ADD10C,0.5*
        ADD11R,1.[REDACTED-UNKNOWN].5*
        ADD12O,1.[REDACTED-UNKNOWN].5*
        ADD13P,1.0X[REDACTED-UNKNOWN]5*
        ADD14C,0.5X0.25*                     (带中心孔)
        ADD20MYMAC,1.0X0.5*                  (宏 + 参数)
        ADD30R,0.[REDACTED-UNKNOWN].[REDACTED-UNKNOWN]Y3I1.0J1.0*          (阵列 SR)
    """
    macros = macros or {}
    body = block.strip().lstrip("%").rstrip("*").strip()
    m = re.match(r"(?:AD)?D?(\d+)([A-Za-z_$][\w$]*)(.*)", body)
    if not m:
        return None
    dcode = int(m.group(1))
    head = m.group(2)
    rest = m.group(3).lstrip(",").strip()

    ap = Aperture(dcode=dcode, raw=body)

    # ---- 阵列 SR: ...X<nx>Y<ny>I<dx>J<dy> ----
    sr_m = re.search(r"X(\d+)Y(\d+)I(" + _NUM + r")J(" + _NUM + r")", rest)
    if sr_m:
        ap.sr = {"nx": int(sr_m.group(1)), "ny": int(sr_m.group(2)),
                 "dx": _f(sr_m.group(3)) * scale, "dy": _f(sr_m.group(4)) * scale}
        rest = rest[:sr_m.start()].strip()

    parts = [p.strip() for p in rest.split("X") if p.strip() != ""]
    nums: List[float] = []
    bad_params: List[str] = []
    for p in parts:
        if re.fullmatch(_NUM, p):
            nums.append(_f(p) * scale)
        else:
            bad_params.append(p)
    if bad_params:
        ap.note = f"光圈参数含无法解析的字段 {bad_params[:2]}, 尺寸可能不完整"

    key = head[0].upper()
    if head in macros:                                 # 宏光圈
        ap.kind = KIND_MACRO
        ap.macro_name = head
        ap.macro = macros[head]
        ap.macro_params = nums
        ap.w = ap.h = 0.0
        g, notes = ap.geometry(0.0, 0.0)
        if g is not None:
            try:
                x0, y0, x1, y1 = g.bounds
                ap.w, ap.h = (x1 - x0), (y1 - y0)
            except Exception:
                pass
        if notes:
            ap.note = "; ".join(notes[:2])
        return ap

    if key == "C":
        ap.kind = KIND_CIRCLE
        ap.w = nums[0] if nums else 0.0
        ap.h = ap.w
        ap.hole = nums[1] if len(nums) > 1 else 0.0
    elif key == "R":
        ap.kind = KIND_RECT
        ap.w = nums[0] if nums else 0.0
        ap.h = nums[1] if len(nums) > 1 else ap.w
        ap.hole = nums[2] if len(nums) > 2 else 0.0
    elif key == "O":
        ap.kind = KIND_OBLONG
        ap.w = nums[0] if nums else 0.0
        ap.h = nums[1] if len(nums) > 1 else ap.w
        ap.hole = nums[2] if len(nums) > 2 else 0.0
    elif key == "P":
        ap.kind = KIND_POLYGON
        ap.w = nums[0] if nums else 0.0
        ap.h = ap.w
        ap.vertices = int(round(nums[1])) if len(nums) > 1 else 4
        ap.rot = nums[2] if len(nums) > 2 else 0.0
        ap.hole = nums[3] if len(nums) > 3 else 0.0
    else:
        ap.kind = KIND_UNKNOWN
        ap.note = f"未知光圈类型 '{head}', 尺寸不可靠"
        ap.w = ap.h = nums[0] if nums else 0.0
    _sanitize(ap)
    return ap


def _sanitize(ap: Aperture) -> None:
    """参数合法性收敛: 中心孔不得吞掉整个光圈, 缺参给出提示。"""
    if ap.kind in (KIND_CIRCLE, KIND_RECT, KIND_OBLONG):
        smallest = min(v for v in (ap.w, ap.h) if v > 0) if (ap.w > 0 or ap.h > 0) else 0.0
        if ap.hole > 0 and smallest > 0 and ap.hole >= smallest:
            ap.note = (ap.note + "; " if ap.note else "") + (
                f"中心孔 {ap.hole:g}mm ≥ 光圈最小尺寸 {smallest:g}mm, 已忽略中心孔")
            ap.hole = 0.0
    if ap.kind in (KIND_RECT, KIND_OBLONG) and ap.h <= 0 and ap.w > 0:
        ap.h = ap.w
    if ap.kind != KIND_UNKNOWN and ap.w <= 0 and ap.kind != KIND_MACRO:
        ap.note = (ap.note + "; " if ap.note else "") + "光圈尺寸缺失或为 0"


