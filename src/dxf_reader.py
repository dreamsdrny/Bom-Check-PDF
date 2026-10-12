"""
Evan_DFM: DXF 导入 (平板/外形设计)
=============================================
轻量 ASCII DXF 解析 (无需 ezdxf):
  支持 LWPOLYLINE / POLYLINE / LINE / CIRCLE / ARC, 提取外形轮廓与几何。
  返回轮廓多边形 (mm) 与包围盒, 供拼版引擎作为 PCS 外形使用。

用法:
    d = read_dxf("board.dxf")
    d["outline"]  -> [(x,y), ...]  mm (归一化到原点)
    d["bbox"]     -> [w, h] mm
"""
import math
from typing import Dict, List, Optional, Tuple


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def read_dxf(path: str) -> dict:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        raw = [ln.rstrip("\r\n") for ln in f]

    pairs = []
    i = 0
    while i + 1 < len(raw):
        pairs.append((raw[i].strip(), raw[i + 1].strip()))
        i += 2

    unit_scale = 1.0
    for code, val in pairs:
        if code == "$INSUNITS":
            unit_scale = 25.4 if val == "1" else (1.0 if val in ("4", "0", "") else 1.0)

    polylines: List[dict] = []
    segments: List[Tuple[float, float, float, float]] = []
    circles: List[Tuple[float, float, float]] = []
    arcs: List[Tuple[float, float, float, float, float]] = []

    cur = None
    lw_pts: List[Tuple[float, float]] = []
    lw_closed = False
    poly_pts: List[Tuple[float, float]] = []
    poly_closed = False
    x = y = x1 = y1 = r = a0 = a1 = 0.0

    def flush():
        nonlocal cur, lw_pts, lw_closed, poly_pts, poly_closed
        if cur == "LWPOLYLINE" and len(lw_pts) >= 2:
            polylines.append({"points": list(lw_pts), "closed": lw_closed})
        elif cur == "POLYLINE" and len(poly_pts) >= 2:
            polylines.append({"points": list(poly_pts), "closed": poly_closed})
        elif cur == "LINE":
            segments.append((x, y, x1, y1))
        elif cur == "CIRCLE":
            circles.append((x, y, r))
        elif cur == "ARC":
            arcs.append((x, y, r, a0, a1))
        lw_pts, lw_closed, poly_pts, poly_closed = [], False, [], False

    for code, val in pairs:
        if code == "0":
            flush()
            cur = val.upper()
            continue
        if cur == "LWPOLYLINE":
            if code == "10":
                v = _num(val)
                if v is not None:
                    lw_pts.append((v, 0.0))
            elif code == "20":
                v = _num(val)
                if v is not None and lw_pts:
                    lw_pts[-1] = (lw_pts[-1][0], v)
            elif code == "70":
                v = _num(val)
                if v is not None:
                    lw_closed = bool(int(v) & 1)
        elif cur == "POLYLINE":
            if code == "70":
                v = _num(val)
                if v is not None:
                    poly_closed = bool(int(v) & 1)
            elif code == "10":
                v = _num(val)
                if v is not None:
                    poly_pts.append((v, 0.0))
            elif code == "20":
                v = _num(val)
                if v is not None and poly_pts:
                    poly_pts[-1] = (poly_pts[-1][0], v)
        elif cur in ("LINE", "CIRCLE", "ARC"):
            if code == "10":
                x = _num(val) or 0.0
            elif code == "20":
                y = _num(val) or 0.0
            elif code == "11":
                x1 = _num(val) or 0.0
            elif code == "21":
                y1 = _num(val) or 0.0
            elif code == "40":
                r = _num(val) or 0.0
            elif code == "50":
                a0 = _num(val) or 0.0
            elif code == "51":
                a1 = _num(val) or 0.0
    flush()

    def scale(pts):
        return [(px * unit_scale, py * unit_scale) for px, py in pts]

    scaled_polys = [{"points": scale(p["points"]), "closed": p["closed"]} for p in polylines]
    scaled_segs = [(a * unit_scale, b * unit_scale, c * unit_scale, d * unit_scale)
                   for a, b, c, d in segments]
    scaled_circles = [(a * unit_scale, b * unit_scale, rr * unit_scale) for a, b, rr in circles]

    outline = None
    best_area = 0.0
    for p in scaled_polys:
        area = abs(_polygon_area(p["points"]))
        if p["closed"] and area > best_area:
            best_area = area
            outline = p["points"]
    if outline is None and scaled_polys:
        outline = max(scaled_polys, key=lambda p: abs(_polygon_area(p["points"])))["points"]

    all_pts: List[Tuple[float, float]] = []
    for p in scaled_polys:
        all_pts += p["points"]
    for a, b, c, d in scaled_segs:
        all_pts += [(a, b), (c, d)]
    for a, b, rr in scaled_circles:
        all_pts += [(a - rr, b - rr), (a + rr, b + rr)]

    if outline:
        all_pts = list(outline)
    if not all_pts:
        return {"outline": None, "bbox": None, "polylines": scaled_polys,
                "segments": scaled_segs, "circles": scaled_circles, "error": "DXF 无有效几何"}

    minx = min(p[0] for p in all_pts)
    miny = min(p[1] for p in all_pts)
    maxx = max(p[0] for p in all_pts)
    maxy = max(p[1] for p in all_pts)
    norm = [(px - minx, py - miny) for px, py in outline] if outline else None
    return {
        "outline": norm,
        "bbox": [round(maxx - minx, 4), round(maxy - miny, 4)],
        "polylines": scaled_polys, "segments": scaled_segs, "circles": scaled_circles,
        "unit_scale": unit_scale,
    }


def _polygon_area(pts: List[Tuple[float, float]]) -> float:
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def dxf_to_svg(outline: List[Tuple[float, float]], width: int = 300) -> str:
    if not outline:
        return "<svg xmlns='http://www.w3.org/2000/svg' width='120' height='40'><text x='5' y='25'>No outline</text></svg>"
    xs = [p[0] for p in outline]
    ys = [p[1] for p in outline]
    w = max(xs) - min(xs) or 1
    h = max(ys) - min(ys) or 1
    s = width / max(w, h)
    pts = " ".join(f"{(x-min(xs))*s+5:.1f},{(h-(y-min(ys)))*s+5:.1f}" for x, y in outline)
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{w*s+10:.0f}' height='{h*s+10:.0f}'>"
            f"<polygon points='{pts}' fill='#c6e0b4' stroke='#385723'/></svg>")