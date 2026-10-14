"""
Asher_DFM 图标生成器
====================
用 PIL 生成清晰的多尺寸 Windows 图标 (Asher_DFM.ico: 16/24/32/48/64/128/256)
以及任务栏用 PNG (Asher_DFM.png, 256x256)。
设计: 圆角方形 + 蓝→青渐变 + 粗体白色 "A" + PCB 走线/过孔点缀 (DFM 主题)。
运行: python make_icon.py
"""
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SIZES = [16, 24, 32, 48, 64, 128, 256]

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\calibrib.ttf",
    r"C:\Windows\Fonts\arial.ttf",
]


def _load_font(size):
    for fp in FONT_CANDIDATES:
        if os.path.isfile(fp):
            try:
                return ImageFont.truetype(fp, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make_master(px=512):
    """渲染高分辨率母版 (再降采样到各尺寸, 保证清晰)"""
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角背景 (蓝 -> 青 对角渐变)
    top = (37, 99, 235)      # #2563EB
    bot = (14, 165, 233)     # #0EA5E9
    radius = int(px * 0.22)
    grad = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(px):
        t = y / max(1, px - 1)
        gd.line([(0, y), (px, y)], fill=_lerp(top, bot, t) + (255,))
    mask = Image.new("L", (px, px), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, px - 1, px - 1], radius=radius, fill=255)
    img.paste(grad, (0, 0), mask)

    # PCB 走线点缀 (半透明浅色, 左上/右下)
    acc = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    ad = ImageDraw.Draw(acc)
    w = max(2, int(px * 0.022))
    line_col = (255, 255, 255, 70)
    node_col = (134, 239, 172, 200)   # 绿
    # 左下走线
    ad.line([(int(px*0.10), int(px*0.72)), (int(px*0.26), int(px*0.72)),
             (int(px*0.34), int(px*0.80)), (int(px*0.34), int(px*0.90))],
            fill=line_col, width=w, joint="curve")
    ad.ellipse([int(px*0.34)-w, int(px*0.90)-w, int(px*0.34)+w, int(px*0.90)+w], fill=node_col)
    # 右上走线
    ad.line([(int(px*0.90), int(px*0.28)), (int(px*0.74), int(px*0.28)),
             (int(px*0.66), int(px*0.20)), (int(px*0.66), int(px*0.10))],
            fill=line_col, width=w, joint="curve")
    ad.ellipse([int(px*0.66)-w, int(px*0.10)-w, int(px*0.66)+w, int(px*0.10)+w], fill=node_col)
    img = Image.alpha_composite(img, acc)
    d = ImageDraw.Draw(img)

    # 粗体 "A" (带轻微阴影增强层次)
    font = _load_font(int(px * 0.62))
    text = "A"
    tb = d.textbbox((0, 0), text, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    tx = (px - tw) / 2 - tb[0]
    ty = (px - th) / 2 - tb[1] - int(px * 0.02)
    # 阴影
    sh = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    ImageDraw.Draw(sh).text((tx + px*0.012, ty + px*0.018), text, font=font, fill=(15, 23, 42, 120))
    sh = sh.filter(ImageFilter.GaussianBlur(px * 0.012))
    img = Image.alpha_composite(img, sh)
    d = ImageDraw.Draw(img)
    d.text((tx, ty), text, font=font, fill=(255, 255, 255, 255))

    # 顶部高光 (玻璃质感)
    gloss = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    gld = ImageDraw.Draw(gloss)
    gld.rounded_rectangle([int(px*0.06), int(px*0.06), int(px*0.94), int(px*0.50)],
                          radius=int(radius*0.9), fill=(255, 255, 255, 26))
    img = Image.alpha_composite(img, gloss)

    # 外描边 (在浅色任务栏上更清晰)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([1, 1, px - 2, px - 2], radius=radius,
                        outline=(255, 255, 255, 60), width=max(2, int(px * 0.008)))
    return img


def main():
    master = make_master(512)
    png_path = os.path.join(HERE, "Asher_DFM.png")
    master.resize((256, 256), Image.LANCZOS).save(png_path, "PNG")

    ico_path = os.path.join(HERE, "Asher_DFM.ico")
    frames = [master.resize((s, s), Image.LANCZOS) for s in SIZES]
    frames[-1].save(ico_path, format="ICO",
                    sizes=[(s, s) for s in SIZES],
                    append_images=frames[:-1])
    print("WROTE", ico_path, os.path.getsize(ico_path), "bytes")
    print("WROTE", png_path, os.path.getsize(png_path), "bytes")


if __name__ == "__main__":
    main()
