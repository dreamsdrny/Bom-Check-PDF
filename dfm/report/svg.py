"""版图 SVG 渲染 — 供 HTML 报告内嵌与「导出矢量图」使用。

渲染内容: 板框 + 各层图形 (走线/焊盘/铺铜) + 钻孔 + DFM 问题标记 (可点击定位)。
坐标单位 mm, 输出 viewBox 亦为 mm, 因此可无损缩放。
"""
from __future__ import annotations

import hashlib
import html as _html
from typing import Iterable, List, Optional, Tuple

from dfm.core.issue import SEVERITY_COLOR

PALETTE = ["#4fc3f7", "#81c784", "#ffb74d", "#ba68c8", "#f06292",
           "#4db6ac", "#aed581", "#ff8a65", "#9575cd", "#a1887f"]
BG = "#101418"
BOARD_BG = "#171d23"
OUTLINE = "#5c6b7a"
HOLE_FILL = "#0b0e11"
HOLE_EDGE = "#8d9aa8"


def layer_color(name: str) -> str:
    """按层名稳定取色 (同名同色, 便于多图对照)"""
    h = int(hashlib.md5(str(name).encode("utf-8")).hexdigest(), 16)
    return PALETTE[h % len(PALETTE)]


def collect_bbox(fab_data: List[dict]) -> Optional[Tuple[float, float, float, float]]:
    boxes = [d.get("bbox") for d in fab_data or []
             if d.get("bbox") and d["bbox"][0] is not None]
    if not boxes:
        return None
    minx = min(b[0] for b in boxes)
    miny = min(b[1] for b in boxes)
    maxx = max(b[2] for b in boxes)
    maxy = max(b[3] for b in boxes)
    if maxx <= minx:
        maxx = minx + 1.0
    if maxy <= miny:
        maxy = miny + 1.0
    return minx, miny, maxx, maxy


def iter_primitives(fab_data: List[dict]):
    for d in fab_data or []:
        if d.get("kind") in ("gerber", "odbpp"):
            for p in d.get("primitives") or d.get("features") or []:
                yield d, p


def layer_names(fab_data: List[dict]) -> List[str]:
    """出现过的层名 (按首次出现顺序)"""
    out: List[str] = []
    seen = set()
    for d, p in iter_primitives(fab_data):
        nm = p.get("layer") or d.get("name") or ""
        if nm and nm not in seen:
            seen.add(nm)
            out.append(nm)
    return out


def render_board_svg(fab_data: List[dict],
                     issues: Optional[List[dict]] = None,
                     width: int = 1200,
                     hidden_layers: Optional[Iterable[str]] = None,
                     max_features: int = 60000,
                     marker_radius: float = 0.0,
                     title: str = "") -> str:
    """渲染版图 + 问题标记为 SVG 字符串。

    :param marker_radius: 问题标记半径 (mm); 0 表示按板尺寸自动取值
    """
    issues = list(issues or [])
    hidden = set(hidden_layers or ())
    bbox = collect_bbox(fab_data)
    if bbox is None:
        return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='120'>"
                f"<rect width='100%' height='100%' fill='{BG}'/>"
                f"<text x='16' y='66' fill='#9aa7b4' font-family='Segoe UI' "
                f"font-size='16'>无几何数据</text></svg>")

    minx, miny, maxx, maxy = bbox
    pad = max((maxx - minx), (maxy - miny)) * 0.03 + 0.5
    vx0, vy0 = minx - pad, miny - pad
    vw, vh = (maxx - minx) + 2 * pad, (maxy - miny) + 2 * pad
    height = max(120, int(width * vh / max(vw, 1e-6)))
    r_marker = marker_radius or max(vw, vh) * 0.008

    parts: List[str] = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' "
        f"viewBox='{vx0:.4f} {vy0:.4f} {vw:.4f} {vh:.4f}' "
        f"style='background:{BG}'>",
        # Y 轴翻转: PCB 坐标 Y 向上
        f"<g transform='translate(0,{2 * vy0 + vh:.4f}) scale(1,-1)'>",
        f"<rect x='{minx:.4f}' y='{miny:.4f}' width='{maxx - minx:.4f}' "
        f"height='{maxy - miny:.4f}' fill='{BOARD_BG}' stroke='{OUTLINE}' "
        f"stroke-width='{max(vw, vh) / 400:.4f}'/>",
    ]

    stroke_w = max(vw, vh) / 1500.0
    count = 0
    for d, p in iter_primitives(fab_data):
        if count >= max_features:
            break
        layer = p.get("layer") or d.get("name") or ""
        if layer in hidden:
            continue
        color = layer_color(layer)
        # 负片图元: 用背景色绘制 (挖除效果), 避免把 anti-pad 画成铜箔
        is_clear = bool(p.get("clear"))
        fill = BG if is_clear else color
        opacity = "1" if is_clear else "0.95"
        t = p.get("type")
        if t in ("draw", "region_start", "arc"):
            if p.get("x1") is None or p.get("x2") is None:
                continue
            w = p.get("width") or stroke_w
            cap = "butt" if p.get("square_cap") else "round"
            parts.append(
                f"<line x1='{p['x1']:.4f}' y1='{p['y1']:.4f}' x2='{p['x2']:.4f}' "
                f"y2='{p['y2']:.4f}' stroke='{fill}' "
                f"stroke-width='{max(w, stroke_w * 0.4):.4f}' "
                f"stroke-linecap='{cap}' opacity='{opacity}'/>")
            count += 1
        elif t == "flash":
            if p.get("x1") is None:
                continue
            parts.append(_flash_svg(p, fill, opacity, stroke_w))
            count += 1
        elif t == "region":
            pts = p.get("points") or []
            if len(pts) >= 3:
                d_path = "M " + " L ".join(f"{q[0]:.4f} {q[1]:.4f}" for q in pts) + " Z"
                for hole in p.get("holes") or []:
                    if len(hole) >= 3:
                        d_path += (" M " + " L ".join(f"{q[0]:.4f} {q[1]:.4f}"
                                                      for q in hole) + " Z")
                fill_rule = " fill-rule='evenodd'" if p.get("holes") else ""
                parts.append(f"<path d='{d_path}' fill='{fill}' "
                             f"fill-opacity='{'1' if is_clear else '0.45'}'{fill_rule} "
                             f"stroke='none'/>")
                count += 1

    for d in fab_data or []:
        if d.get("kind") != "drill":
            continue
        for h in d.get("holes", []):
            if h.get("x") is None:
                continue
            r = max((h.get("diameter_mm") or 0.3) / 2.0, stroke_w * 0.4)
            if h.get("is_slot") and h.get("x2") is not None:
                # 槽孔: 以线段 + 圆头端帽绘制
                parts.append(
                    f"<line x1='{h['x']:.4f}' y1='{h['y']:.4f}' x2='{h['x2']:.4f}' "
                    f"y2='{h['y2']:.4f}' stroke='{HOLE_EDGE}' "
                    f"stroke-width='{2 * r:.4f}' stroke-linecap='round'/>")
                continue
            parts.append(f"<circle cx='{h['x']:.4f}' cy='{h['y']:.4f}' r='{r:.4f}' "
                         f"fill='{HOLE_FILL}' stroke='{HOLE_EDGE}' "
                         f"stroke-width='{stroke_w * 0.6:.4f}'/>")

    parts.append("</g>")

    # ---- 问题标记 (不翻转, 用屏幕坐标计算) ----
    def sy(y: float) -> float:
        return (2 * vy0 + vh) - y

    for i, it in enumerate(issues):
        x, y = it.get("x"), it.get("y")
        if x is None or y is None:
            continue
        color = "#" + SEVERITY_COLOR.get(it.get("severity"), "999999")
        parts.append(
            f"<circle class='dfm-marker' data-idx='{i}' cx='{x:.4f}' cy='{sy(y):.4f}' "
            f"r='{r_marker:.4f}' fill='{color}' fill-opacity='0.85' stroke='#ffffff' "
            f"stroke-width='{r_marker * 0.25:.4f}'/>")

    if title:
        parts.append(f"<text x='{vx0 + pad * 0.4:.4f}' y='{vy0 + pad * 0.7:.4f}' "
                     f"fill='#9aa7b4' font-size='{max(vh * 0.03, 1):.3f}' "
                     f"font-family='Segoe UI'>{_html.escape(title)}</text>")
    parts.append("</svg>")
    return "".join(parts)


def _flash_svg(p: dict, fill: str, opacity: str, stroke_w: float) -> str:
    """按真实形状渲染一个焊盘闪点 (圆/矩形/椭圆/多边形, 支持旋转)。"""
    x, y = float(p["x1"]), float(p["y1"])
    shape = str(p.get("shape") or "").lower()
    w = float(p.get("w") or p.get("width") or 0.0)
    h = float(p.get("h") or p.get("width") or w or 0.0)
    rot = float(p.get("rot") or 0.0)
    min_dim = max(min(w, h) if min(w, h) > 0 else max(w, h), stroke_w)

    pts = p.get("points")
    if (shape == "poly" or pts) and pts and len(pts) >= 3:
        d_path = "M " + " L ".join(f"{q[0]:.4f} {q[1]:.4f}" for q in pts) + " Z"
        return f"<path d='{d_path}' fill='{fill}' opacity='{opacity}'/>"
    if shape in ("rect", "rectangle", "square") and w > 0:
        transform = f" transform='rotate({-rot:.3f} {x:.4f} {y:.4f})'" if rot else ""
        return (f"<rect x='{x - w / 2:.4f}' y='{y - h / 2:.4f}' width='{w:.4f}' "
                f"height='{h:.4f}' fill='{fill}' opacity='{opacity}'{transform}/>")
    if shape in ("oval", "oblong", "roundrect", "ellipse") and w > 0:
        transform = f" transform='rotate({-rot:.3f} {x:.4f} {y:.4f})'" if rot else ""
        return (f"<ellipse cx='{x:.4f}' cy='{y:.4f}' rx='{w / 2:.4f}' ry='{h / 2:.4f}' "
                f"fill='{fill}' opacity='{opacity}'{transform}/>")
    r = max(min_dim / 2.0, stroke_w * 0.5)
    return (f"<circle cx='{x:.4f}' cy='{y:.4f}' r='{r:.4f}' fill='{fill}' "
            f"opacity='{opacity}'/>")


def legend_items(fab_data: List[dict], hidden: Optional[Iterable[str]] = None) -> List[dict]:
    """图例: [{layer, color, hidden}]"""
    hidden = set(hidden or ())
    return [{"layer": nm, "color": layer_color(nm), "hidden": nm in hidden}
            for nm in layer_names(fab_data)]


def save_svg(svg: str, path: str) -> str:
    import os

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg)
    return path
