"""
BRD-AI: DFM 报告截图渲染 (PIL)
=============================================================
为 Valor 式报告的「问题图片」列 / 问题明细页 / 封面生成版图截图:
  - render_overview_png(): 整板概览 + 全部问题标记 (封面用)
  - render_crop_png():     以问题 XY 为中心的局部放大图 + 标记 (逐问题用)

输入为 src.brd_geometry.to_fab_data 产出的结构 (odbpp features + drill holes),
深色底、按层配色, 与 fab_viewer 观感一致。无第三方依赖 (仅 Pillow)。
"""
import hashlib
import os
from typing import List, Optional

from PIL import Image, ImageDraw

from src.dfm_rules import SEVERITY_COLOR

_PALETTE = [(192, 0, 0), (10, 122, 58), (184, 134, 11), (47, 84, 150),
            (112, 48, 160), (0, 131, 143), (109, 76, 65), (69, 90, 100),
            (173, 20, 87), (51, 105, 30), (0, 150, 136), (233, 30, 99)]
_BG = (18, 18, 22)
_PANEL = (30, 30, 36)


def _layer_color(name: str):
    h = int(hashlib.md5(str(name).encode("utf-8")).hexdigest(), 16)
    return _PALETTE[h % len(_PALETTE)]


def _hex_to_rgb(hx: str):
    hx = hx.lstrip("#")
    if len(hx) != 6:
        return (150, 150, 150)
    return tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4))


def _collect_bbox(fab_data: List[dict]):
    boxes = [d.get("bbox") for d in fab_data if d.get("bbox") and d["bbox"][0] is not None]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _iter_features(fab_data: List[dict]):
    for d in fab_data:
        if d.get("kind") in ("gerber", "odbpp"):
            for p in (d.get("features") or d.get("primitives") or []):
                yield p
        elif d.get("kind") == "drill":
            for h in d.get("holes", []):
                yield {"type": "hole", "x1": h.get("x"), "y1": h.get("y"),
                       "width": h.get("diameter_mm")}


def _make_transform(wx0, wy0, wx1, wy1, size):
    spanx = max(wx1 - wx0, 1e-6)
    spany = max(wy1 - wy0, 1e-6)
    scale = min(size / spanx, size / spany)
    # 居中
    ox = (size - spanx * scale) / 2.0
    oy = (size - spany * scale) / 2.0

    def tx(x):
        return (x - wx0) * scale + ox

    def ty(y):
        return size - ((y - wy0) * scale + oy)

    return tx, ty, scale


def _draw_features(dr, fab_data, tx, ty, scale, wx0, wy0, wx1, wy1, max_items=20000):
    n = 0
    for p in _iter_features(fab_data):
        if n >= max_items:
            break
        x1, y1 = p.get("x1"), p.get("y1")
        if x1 is None or y1 is None:
            continue
        # 视窗裁剪 (含少量余量)
        if not (wx0 - 2 <= x1 <= wx1 + 2 and wy0 - 2 <= y1 <= wy1 + 2):
            x2, y2 = p.get("x2"), p.get("y2")
            if x2 is None or not (wx0 - 2 <= x2 <= wx1 + 2 and wy0 - 2 <= y2 <= wy1 + 2):
                continue
        t = p.get("type")
        layer = p.get("layer", "")
        col = _layer_color(layer) if t != "hole" else (120, 120, 130)
        if t in ("draw", "arc"):
            x2, y2 = p.get("x2", x1), p.get("y2", y1)
            w = max(1, int(round((p.get("width") or 0.1) * scale * 0.5)))
            dr.line([tx(x1), ty(y1), tx(x2), ty(y2)], fill=col, width=w)
            n += 1
        elif t in ("flash", "hole"):
            r = max(1.0, (p.get("width") or 0.3) / 2.0 * scale)
            dr.ellipse([tx(x1) - r, ty(y1) - r, tx(x1) + r, ty(y1) + r],
                       fill=col if t == "flash" else _BG, outline=col)
            n += 1
        elif t == "region":
            pts = p.get("points") or []
            if len(pts) >= 3:
                coords = []
                for (px, py) in pts:
                    coords.extend([tx(px), ty(py)])
                dr.polygon(coords, fill=col)
                n += 1


def _draw_marker(dr, tx, ty, x, y, rgb, size, cross=True):
    cx, cy = tx(x), ty(y)
    r = size
    if cross:
        dr.line([cx - r * 2.2, cy, cx + r * 2.2, cy], fill=(255, 255, 255), width=1)
        dr.line([cx, cy - r * 2.2, cx, cy + r * 2.2], fill=(255, 255, 255), width=1)
    dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=rgb, outline=(255, 255, 255), width=2)


def render_overview_png(fab_data: List[dict], issues: List[dict], out_png: str,
                        width: int = 720) -> Optional[str]:
    """整板概览 + 问题标记"""
    bbox = _collect_bbox(fab_data)
    img = Image.new("RGB", (width, width), _BG)
    dr = ImageDraw.Draw(img)
    if bbox is None:
        dr.text((12, width // 2), "No geometry", fill=(200, 200, 200))
        _save(img, out_png)
        return out_png
    pad = max((bbox[2] - bbox[0]), (bbox[3] - bbox[1])) * 0.03 + 0.5
    wx0, wy0, wx1, wy1 = bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad
    tx, ty, scale = _make_transform(wx0, wy0, wx1, wy1, width)
    dr.rectangle([0, 0, width - 1, width - 1], outline=(60, 60, 70))
    _draw_features(dr, fab_data, tx, ty, scale, wx0, wy0, wx1, wy1)
    for it in issues:
        x, y = it.get("x"), it.get("y")
        if x is None or y is None:
            continue
        rgb = _hex_to_rgb(SEVERITY_COLOR.get(it.get("severity"), "FF0000"))
        _draw_marker(dr, tx, ty, x, y, rgb, max(3, int(width * 0.008)), cross=False)
    _save(img, out_png)
    return out_png


def render_crop_png(fab_data: List[dict], cx: float, cy: float, out_png: str,
                    half_span_mm: float = 6.0, size: int = 360,
                    marker_rgb=(255, 0, 0)) -> Optional[str]:
    """以 (cx,cy) 为中心的局部放大图 + 中心标记"""
    img = Image.new("RGB", (size, size), _BG)
    dr = ImageDraw.Draw(img)
    if cx is None or cy is None:
        dr.text((10, size // 2), "No location", fill=(200, 200, 200))
        _save(img, out_png)
        return out_png
    wx0, wy0 = cx - half_span_mm, cy - half_span_mm
    wx1, wy1 = cx + half_span_mm, cy + half_span_mm
    tx, ty, scale = _make_transform(wx0, wy0, wx1, wy1, size)
    dr.rectangle([0, 0, size - 1, size - 1], outline=(70, 70, 80))
    _draw_features(dr, fab_data, tx, ty, scale, wx0, wy0, wx1, wy1)
    _draw_marker(dr, tx, ty, cx, cy, marker_rgb, max(5, int(size * 0.03)))
    _save(img, out_png)
    return out_png


def _save(img: Image.Image, out_png: str):
    d = os.path.dirname(os.path.abspath(out_png))
    if d:
        os.makedirs(d, exist_ok=True)
    img.save(out_png, "PNG")
