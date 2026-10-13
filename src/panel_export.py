"""
Evan_DFM: 拼版输出 (图片 PNG / PDF)
=============================================
将拼版方案的三图 (连板图 / 工作稿图 / 开料图) 渲染为位图并导出:
  - PNG 图片 (每图一张 + 合并一张)
  - PDF 文档 (三图分页)
可选叠加 PDF/DXF 参考底图。

依赖: Pillow (PIL)。无需 reportlab / cairosvg。
"""
import logging
import os
from typing import Dict, List, Optional

from PIL import Image, ImageDraw

from src.panel_engine import format_dim

logger = logging.getLogger(__name__)

_FEATURE_FILL = {
    "rib": (10, 122, 58),
    "rail": (189, 189, 189),
    "impedance": (126, 87, 194),
    "leftover": (217, 217, 217),
    "mark": (255, 217, 102),
    "tooling": (142, 169, 219),
}


def _hex_to_rgb(color: str):
    color = (color or "#2f7ed8").lstrip("#")
    if len(color) == 3:
        color = "".join(c * 2 for c in color)
    try:
        return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (47, 126, 216)


def view_to_image(view: dict, width_px: int = 1300,
                  bg: Optional[Image.Image] = None,
                  bg_opacity: float = 0.35,
                  margin: int = 24) -> Image.Image:
    cw, ch = view["container"]
    cw = max(cw, 1e-6)
    ch = max(ch, 1e-6)
    scale = width_px / max(cw, ch)
    W = int(cw * scale) + 2 * margin
    H = int(ch * scale) + 2 * margin + 26
    img = Image.new("RGB", (W, H), (250, 250, 250))
    draw = ImageDraw.Draw(img, "RGBA")
    ox, oy = margin, margin

    def tx(x):
        return ox + x * scale

    def ty(y):
        return oy + (ch - y) * scale

    if bg is not None:
        try:
            bw, bh = int(cw * scale), int(ch * scale)
            b = bg.convert("RGB").resize((max(1, bw), max(1, bh)))
            base = img.crop((ox, oy, ox + max(1, bw), oy + max(1, bh)))
            img.paste(Image.blend(base, b, bg_opacity), (ox, oy))
        except Exception as exc:  # noqa: BLE001
            logger.warning("参考底图叠加失败, 已跳过: %s", exc)

    draw.rectangle([tx(0), ty(ch), tx(cw), ty(0)], outline=(50, 50, 50))

    for f in view.get("features", []):
        t = f.get("type")
        if t == "vcut":
            draw.line([tx(f["x1"]), ty(f["y1"]), tx(f["x2"]), ty(f["y2"])], fill=(198, 40, 40), width=1)
        elif t == "stamp":
            r = 1.6
            draw.ellipse([tx(f["x"]) - r, ty(f["y"]) - r, tx(f["x"]) + r, ty(f["y"]) + r], fill=(85, 85, 85))
        elif t == "break_off":
            draw.rectangle([tx(0), ty(f["h"]), tx(f["w"]), ty(0)], outline=(10, 122, 58), width=2)
        elif t == "rib":
            draw.rectangle([tx(f["x"]), ty(f["y"] + f["h"]), tx(f["x"] + f["w"]), ty(f["y"])],
                           fill=_FEATURE_FILL["rib"])
        elif t == "rail":
            draw.rectangle([tx(f["x"]), ty(f["y"] + f["h"]), tx(f["x"] + f["w"]), ty(f["y"])],
                           fill=_FEATURE_FILL["rail"])
        elif t == "impedance":
            draw.rectangle([tx(f["x"] - f["w"] / 2), ty(f["y"] + f["h"]),
                            tx(f["x"] + f["w"] / 2), ty(f["y"])], fill=_FEATURE_FILL["impedance"])
        elif t == "mark":
            r = 3
            draw.ellipse([tx(f["x"]) - r, ty(f["y"]) - r, tx(f["x"]) + r, ty(f["y"]) + r],
                         fill=(255, 217, 102), outline=(191, 143, 0))
        elif t == "tooling":
            r = 2.6
            draw.ellipse([tx(f["x"]) - r, ty(f["y"]) - r, tx(f["x"]) + r, ty(f["y"]) + r],
                         fill=(142, 169, 219), outline=(47, 84, 150))
        elif t == "direction":
            draw.polygon([tx(f["x"]), ty(f["y"]), tx(f["x"] + 3), ty(f["y"]), tx(f["x"]), ty(f["y"] + 3)],
                         fill=(192, 0, 0))
        elif t == "leftover":
            draw.rectangle([tx(f["x"]), ty(f["y"] + f["h"]), tx(f["x"] + f["w"]), ty(f["y"])],
                           fill=_FEATURE_FILL["leftover"], outline=(150, 150, 150))
        elif t == "dim":
            x1, y1, x2, y2 = tx(f["x1"]), ty(f["y1"]), tx(f["x2"]), ty(f["y2"])
            txt = format_dim(f.get("value"), view.get("unit", "mm"))
            if f.get("label"):
                txt = f"{f['label']} {txt}"
            if f.get("orient") == "v":
                xx = x1 - 16
                draw.line([xx, y1, xx, y2], fill=(192, 0, 0))
                draw.line([xx - 4, y1, xx + 4, y1], fill=(192, 0, 0))
                draw.line([xx - 4, y2, xx + 4, y2], fill=(192, 0, 0))
                draw.text((xx - 4, (y1 + y2) / 2), txt, fill=(192, 0, 0), anchor="mm")
            else:
                yy = y1 + 16
                draw.line([x1, yy, x2, yy], fill=(192, 0, 0))
                draw.line([x1, yy - 4, x1, yy + 4], fill=(192, 0, 0))
                draw.line([x2, yy - 4, x2, yy + 4], fill=(192, 0, 0))
                draw.text(((x1 + x2) / 2, yy + 12), txt, fill=(192, 0, 0), anchor="mm")

    for it in view.get("items", []):
        if it.get("hidden"):
            continue
        color = _hex_to_rgb(it.get("color", "#2f7ed8"))
        frame = it.get("frame")
        fill = None if frame else color + (140,)
        outline = (0, 0, 0) if frame else color
        poly = it.get("poly")
        if poly:
            pts = [(tx(px), ty(py)) for px, py in poly]
            draw.polygon(pts, fill=fill, outline=outline)
        else:
            x, y, w, h = it["x"], it["y"], it["w"], it["h"]
            draw.rectangle([tx(x), ty(y + h), tx(x + w), ty(y)], fill=fill, outline=outline)
        if it.get("mirror"):
            draw.line([tx(it["x"]), ty(it["y"]), tx(it["x"] + it["w"]), ty(it["y"] + it["h"])],
                      fill=(192, 0, 0), width=2)
        label = it.get("label", "")
        if label and it["w"] * scale > 14:
            txm, tym = tx(it["x"] + it["w"] / 2), ty(it["y"] + it["h"] / 2)
            try:
                draw.text((txm, tym), label, fill=outline, anchor="mm")
            except Exception as exc:  # noqa: BLE001
                logger.warning("标签绘制失败, 已跳过: %s", exc)

    draw.text((margin, H - 20), view.get("title", ""), fill=(40, 40, 40))
    return img


def render_plan_images(plan: dict, width_px: int = 1300,
                       backgrounds: Optional[Dict[str, Image.Image]] = None) -> Dict[str, Image.Image]:
    backgrounds = backgrounds or {}
    imgs = {}
    if not plan.get("ok"):
        return imgs
    for key in ("pcs", "array", "panel"):
        view = plan["views"][key]
        imgs[key] = view_to_image(view, width_px, backgrounds.get(key))
    return imgs


def export_png(plan: dict, out_dir: str, width_px: int = 1300,
               backgrounds: Optional[Dict[str, Image.Image]] = None) -> Dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    imgs = render_plan_images(plan, width_px, backgrounds)
    paths = {}
    for key, im in imgs.items():
        p = os.path.join(out_dir, f"panel_{key}.png")
        im.save(p)
        paths[key] = p
    if imgs:
        combined = _combine_vertical(list(imgs.values()))
        cp = os.path.join(out_dir, "panel_all.png")
        combined.save(cp)
        paths["all"] = cp
    return paths


def export_pdf(plan: dict, out_path: str, width_px: int = 1400,
               backgrounds: Optional[Dict[str, Image.Image]] = None,
               combined: bool = False) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    imgs = render_plan_images(plan, width_px, backgrounds)
    if not imgs:
        return out_path
    if combined:
        page = _combine_vertical(list(imgs.values()))
        page.save(out_path, "PDF", resolution=150.0)
    else:
        pages = [im.convert("RGB") for im in imgs.values()]
        pages[0].save(out_path, "PDF", resolution=150.0, save_all=True, append_images=pages[1:])
    return out_path


def _combine_vertical(images: List[Image.Image]) -> Image.Image:
    width = max(im.width for im in images)
    total_h = sum(im.height for im in images)
    canvas = Image.new("RGB", (width, total_h), (255, 255, 255))
    y = 0
    for im in images:
        canvas.paste(im, (0, y))
        y += im.height
    return canvas