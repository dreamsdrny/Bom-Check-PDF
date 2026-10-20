"""矢量图标 — 内联 SVG, 按目标尺寸与设备像素比渲染, 任意 DPI 下都清晰。

不依赖任何外部图标文件/图标字体, 便于打包与二次开发替换:
需要换图标时只改 :data:`SVG_PATHS` 中对应条目即可。

用法::

    from dfm.ui.icons import icon
    btn.setIcon(icon("run", "#3fb950", 18))
"""
from __future__ import annotations

from typing import Dict, Optional

_CACHE: Dict[tuple, object] = {}

#: 24x24 视框, 描边式图标 (Feather 风格)
SVG_PATHS: Dict[str, str] = {
    # ---- 导航 ----
    "home": "<path d='M3.5 10.6 12 3.5l8.5 7.1'/><path d='M5.8 9.6V20.5h12.4V9.6'/>"
            "<path d='M10 20.5v-5.2h4v5.2'/>",
    "grid": "<rect x='3.5' y='3.5' width='7' height='7' rx='1.6'/>"
            "<rect x='13.5' y='3.5' width='7' height='7' rx='1.6'/>"
            "<rect x='3.5' y='13.5' width='7' height='7' rx='1.6'/>"
            "<rect x='13.5' y='13.5' width='7' height='7' rx='1.6'/>",
    "project": "<path d='M3.5 7.2A1.7 1.7 0 0 1 5.2 5.5h3.6l1.9 2.3h8.1a1.7 1.7 0 0 1 1.7 1.7v8.3"
               "a1.7 1.7 0 0 1-1.7 1.7H5.2a1.7 1.7 0 0 1-1.7-1.7z'/>"
               "<path d='M12 11.4v5.4'/><path d='M9.6 14.4 12 16.8l2.4-2.4'/>",
    "chip": "<rect x='6.5' y='6.5' width='11' height='11' rx='2'/>"
            "<rect x='10' y='10' width='4' height='4' rx='1'/>"
            "<path d='M9.5 3.5v3M14.5 3.5v3M9.5 17.5v3M14.5 17.5v3"
            "M3.5 9.5h3M3.5 14.5h3M17.5 9.5h3M17.5 14.5h3'/>",
    "run": "<path d='M8 5.2 19 12 8 18.8z' fill='{color}' stroke='none'/>",
    "results": "<path d='M8.4 6.2h12M8.4 12h12M8.4 17.8h12'/>"
               "<circle cx='4.2' cy='6.2' r='1.1' fill='{color}'/>"
               "<circle cx='4.2' cy='12' r='1.1' fill='{color}'/>"
               "<circle cx='4.2' cy='17.8' r='1.1' fill='{color}'/>",
    "layers": "<path d='M12 3.2 3.4 7.6 12 12l8.6-4.4z'/><path d='M3.4 12.4 12 16.8l8.6-4.4'/>"
              "<path d='M3.4 16.9 12 21.3l8.6-4.4'/>",
    "sliders": "<path d='M4 7h9M17.5 7H20M4 12h3.5M12 12h8M4 17h9M17.5 17H20'/>"
               "<circle cx='15.2' cy='7' r='2.1'/><circle cx='9.7' cy='12' r='2.1'/>"
               "<circle cx='15.2' cy='17' r='2.1'/>",
    "report": "<path d='M14 3.5H7.2a1.7 1.7 0 0 0-1.7 1.7v13.6a1.7 1.7 0 0 0 1.7 1.7h9.6"
              "a1.7 1.7 0 0 0 1.7-1.7V8z'/><path d='M13.8 3.6V8h4.4'/>"
              "<path d='M8.8 13h6.4M8.8 16.6h6.4'/>",
    "gear": "<circle cx='12' cy='12' r='3.3'/>"
            "<path d='M12 2.8v2.6M12 18.6v2.6M2.8 12h2.6M18.6 12h2.6M5.5 5.5l1.9 1.9"
            "M16.6 16.6l1.9 1.9M18.5 5.5l-1.9 1.9M7.4 16.6l-1.9 1.9'/>",
    "plugin": "<path d='M9.2 3.4v5.4M14.8 3.4v5.4'/><path d='M6.2 8.8h11.6v2.6a5.8 5.8 0 0 1-11.6 0z'/>"
              "<path d='M12 17.2v3.4'/>",
    "book": "<path d='M4.4 4.6A2.1 2.1 0 0 1 6.5 2.5H20v16.2H6.5a2.1 2.1 0 0 0-2.1 2.1z'/>"
            "<path d='M4.4 18.7V4.6'/><path d='M8.4 7h7.6M8.4 10.6h7.6'/>",
    # ---- 操作 ----
    "folder": "<path d='M3.5 7.2A1.7 1.7 0 0 1 5.2 5.5h3.6l1.9 2.3h8.1a1.7 1.7 0 0 1 1.7 1.7v8.3"
              "a1.7 1.7 0 0 1-1.7 1.7H5.2a1.7 1.7 0 0 1-1.7-1.7z'/>",
    "file": "<path d='M14 3.5H7.2a1.7 1.7 0 0 0-1.7 1.7v13.6a1.7 1.7 0 0 0 1.7 1.7h9.6"
            "a1.7 1.7 0 0 0 1.7-1.7V8z'/><path d='M13.8 3.6V8h4.4'/>",
    "search": "<circle cx='10.8' cy='10.8' r='6.6'/><path d='M20 20l-4.6-4.6'/>",
    "filter": "<path d='M3.6 5.2h16.8l-6.6 7.7v6.1l-3.6 1.8v-7.9z'/>",
    "refresh": "<path d='M20.2 12a8.2 8.2 0 1 1-2.5-5.9'/><path d='M20.4 3.6v4.8h-4.8'/>",
    "export": "<path d='M12 3.6v11.6'/><path d='M7.6 10.8 12 15.2l4.4-4.4'/>"
              "<path d='M4.2 19.8h15.6'/>",
    "save": "<path d='M5.2 3.6h10.4l3.2 3.2v13.6H5.2z'/><path d='M8.6 3.6v5.6h6.8V3.6'/>"
            "<path d='M8.6 20.4v-6h6.8v6'/>",
    "zoom-in": "<circle cx='10.8' cy='10.8' r='6.6'/><path d='M20 20l-4.6-4.6'/>"
               "<path d='M10.8 8.2v5.2M8.2 10.8h5.2'/>",
    "zoom-out": "<circle cx='10.8' cy='10.8' r='6.6'/><path d='M20 20l-4.6-4.6'/>"
                "<path d='M8.2 10.8h5.2'/>",
    "fit": "<path d='M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5'/>",
    "close": "<path d='M6.2 6.2 17.8 17.8M17.8 6.2 6.2 17.8'/>",
    "add": "<path d='M12 5.2v13.6M5.2 12h13.6'/>",
    "trash": "<path d='M4.2 7h15.6M9.4 7V4.2h5.2V7M6.4 7l1 13.4h9.2L17.6 7'/>",
    "edit": "<path d='M4.2 19.8h4L19.6 8.4l-4-4L4.2 15.8z'/><path d='M14.4 5.6l4 4'/>",
    "info": "<circle cx='12' cy='12' r='8.6'/><path d='M12 11.2v5.4'/>"
            "<circle cx='12' cy='7.9' r='1' fill='{color}'/>",
    "warning": "<path d='M12 3.6 21.6 20.4H2.4z'/><path d='M12 9.4v4.8'/>"
               "<circle cx='12' cy='17.2' r='1' fill='{color}'/>",
    "error": "<circle cx='12' cy='12' r='8.6'/><path d='M15.1 8.9 8.9 15.1M8.9 8.9l6.2 6.2'/>",
    "success": "<circle cx='12' cy='12' r='8.6'/><path d='M8.2 12.4l2.6 2.6 5-5.4'/>",
    "check": "<path d='M4.8 12.6 9.6 17.4 19.2 6.6'/>",
    "chevron-right": "<path d='M9.4 5.6 15.8 12l-6.4 6.4'/>",
    "chevron-down": "<path d='M5.6 9.4 12 15.8l6.4-6.4'/>",
    "external": "<path d='M14 4h6v6'/><path d='M20 4l-8.6 8.6'/>"
                "<path d='M18 14.4v5.2H4.4V6h5.2'/>",
    "clock": "<circle cx='12' cy='12' r='8.6'/><path d='M12 7.2V12l3.2 2'/>",
    "eye": "<path d='M2.4 12S6 6.2 12 6.2 21.6 12 21.6 12 18 17.8 12 17.8 2.4 12 2.4 12z'/>"
           "<circle cx='12' cy='12' r='2.9'/>",
    "palette": "<circle cx='12' cy='12' r='8.6'/><path d='M12 3.4a8.6 8.6 0 0 0 0 17.2z' "
               "fill='{color}' stroke='none'/>",
    "stop": "<rect x='6.4' y='6.4' width='11.2' height='11.2' rx='2' fill='{color}' stroke='none'/>",
    "target": "<circle cx='12' cy='12' r='8.4'/><circle cx='12' cy='12' r='4'/>"
              "<circle cx='12' cy='12' r='1' fill='{color}'/>",
    "link": "<path d='M10.2 13.8a3.6 3.6 0 0 0 5.1 0l2.7-2.7a3.6 3.6 0 0 0-5.1-5.1l-1.3 1.3'/>"
            "<path d='M13.8 10.2a3.6 3.6 0 0 0-5.1 0L6 12.9a3.6 3.6 0 0 0 5.1 5.1l1.3-1.3'/>",
    "list-tree": "<path d='M5 4.6h6.4M5 12h6.4M5 19.4h6.4'/><path d='M14.6 8.4h5.2v7.2h-5.2'/>",
    "database": "<ellipse cx='12' cy='6' rx='7.6' ry='3'/><path d='M4.4 6v12c0 1.7 3.4 3 7.6 3"
                "s7.6-1.3 7.6-3V6'/><path d='M4.4 12c0 1.7 3.4 3 7.6 3s7.6-1.3 7.6-3'/>",
    "gauge": "<path d='M4 17.6a9 9 0 1 1 16 0'/><path d='M12 17.6 16.4 10'/>"
             "<circle cx='12' cy='17.6' r='1.4' fill='{color}'/>",
}

#: 应用 Logo (填充式)
LOGO_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
    "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
    "<stop offset='0' stop-color='#4c8dff'/><stop offset='1' stop-color='#7c5cff'/>"
    "</linearGradient></defs>"
    "<rect x='2' y='2' width='60' height='60' rx='16' fill='url(#g)'/>"
    "<g fill='none' stroke='#ffffff' stroke-width='3.2' stroke-linecap='round'>"
    "<path d='M16 20h20M16 32h14M16 44h24'/></g>"
    "<circle cx='46' cy='32' r='5.4' fill='#ffffff'/>"
    "<circle cx='46' cy='32' r='2.2' fill='#4c8dff'/></svg>"
)


def svg_text(name: str, color: str = "#e6edf3", stroke_width: float = 1.7,
             size: int = 24) -> str:
    """返回完整 SVG 文本 (name 见 :data:`SVG_PATHS`)。"""
    body = SVG_PATHS.get(name, "")
    if not body:
        body = SVG_PATHS["info"]
    body = body.replace("{color}", color)
    return (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' "
            f"width='{size}' height='{size}' fill='none' stroke='{color}' "
            f"stroke-width='{stroke_width}' stroke-linecap='round' "
            f"stroke-linejoin='round'>{body}</svg>")


def logo_svg(size: int = 64) -> str:
    return LOGO_SVG.replace("width='64'", f"width='{size}'")


def _renderer(svg: str):
    from PySide6.QtCore import QByteArray
    from PySide6.QtSvg import QSvgRenderer

    return QSvgRenderer(QByteArray(svg.encode("utf-8")))


def pixmap(name: str, color: str = "#e6edf3", size: int = 20,
           stroke_width: float = 1.7, dpr: Optional[float] = None):
    """渲染为 QPixmap (考虑设备像素比, 高分屏下同样锐利)。"""
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtWidgets import QApplication

    if dpr is None:
        app = QApplication.instance()
        dpr = app.devicePixelRatio() if app else 1.0
    key = (name, color, size, round(stroke_width, 2), round(dpr, 2))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    svg = svg_text(name, color, stroke_width, size)
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(0)
    renderer = _renderer(svg)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    renderer.render(painter)
    painter.end()
    _CACHE[key] = pm
    return pm


def icon(name: str, color: str = "#e6edf3", size: int = 20,
         stroke_width: float = 1.7):
    """渲染为 QIcon。"""
    from PySide6.QtGui import QIcon

    key = ("icon", name, color, size, stroke_width)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    ic = QIcon()
    for mult in (1.0, 1.5, 2.0):
        ic.addPixmap(pixmap(name, color, int(size * mult), stroke_width, dpr=1.0))
    _CACHE[key] = ic
    return ic


def logo_pixmap(size: int = 44):
    """应用 Logo (渐变方块)。"""
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    dpr = app.devicePixelRatio() if app else 1.0
    key = ("logo", size, round(dpr, 2))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(0)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    _renderer(LOGO_SVG).render(painter)
    painter.end()
    _CACHE[key] = pm
    return pm


def app_icon():
    """窗口/任务栏图标 (多尺寸)。"""
    from PySide6.QtGui import QIcon

    key = ("app-icon",)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        pm = logo_pixmap(s)
        ic.addPixmap(pm)
    _CACHE[key] = ic
    return ic


def clear_cache() -> None:
    _CACHE.clear()
